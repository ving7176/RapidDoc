# Copyright (c) RapidAI. All rights reserved.
"""
模型输出转换为中间 JSON 格式
"""
import os
from tqdm import tqdm
from loguru import logger

from rapid_doc.backend.utils.utils import cross_page_table_merge
from rapid_doc.model.custom import CustomBaseModel
from rapid_doc.utils.char_utils import full_to_half
from rapid_doc.utils.config_reader import get_device, get_formula_enable
from rapid_doc.backend.pipeline.model_init import AtomModelSingleton
from rapid_doc.backend.pipeline.para_split import para_split
from rapid_doc.utils.block_pre_proc import prepare_block_bboxes, process_groups
from rapid_doc.utils.block_sort import sort_blocks_by_bbox
from rapid_doc.utils.cut_image import cut_image_and_table
from rapid_doc.utils.enum_class import BlockType, ContentType
from rapid_doc.utils.model_utils import clean_memory
from rapid_doc.backend.pipeline.pipeline_magic_model import MagicModel
from rapid_doc.utils.ocr_utils import OcrConfidence
from rapid_doc.utils.pdf_image_tools import save_table_fill_image
from rapid_doc.utils.span_block_fix import fill_spans_in_blocks, fix_discarded_block, fix_block_spans
from rapid_doc.utils.span_pre_proc import (
    remove_outside_spans, remove_overlaps_low_confidence_spans,
    remove_overlaps_min_spans, txt_spans_extract
)
from rapid_doc.version import __version__
from rapid_doc.utils.hash_utils import bytes_md5


def page_model_info_to_page_info(
    page_model_info,
    image_dict,
    page_dict,
    image_writer,
    page_index,
    ocr_enable=False,
    formula_enabled=True,
    image_config=None,
    use_vl_ocr=False
):
    """
    将单页模型输出转换为页面信息
    
    Args:
        page_model_info: 模型输出信息
        image_dict: 图像字典
        page_dict: PDF 页面字典
        image_writer: 图像写入器
        page_index: 页面索引
        ocr_enable: 是否启用 OCR
        formula_enabled: 是否启用公式识别
        image_config: 图像配置
        use_vl_ocr: 是否使用 VL OCR 模式
        
    Returns:
        页面信息字典
    """
    scale = image_dict["scale"]
    page_pil_img = image_dict["img_pil"]
    page_img_md5 = bytes_md5(page_pil_img.tobytes())
    page_w, page_h = map(int, page_dict['size'])
    
    magic_model = MagicModel(page_model_info, scale)
    
    # 图像配置
    extract_original_image = image_config.get("extract_original_image", False) if image_config else False
    extract_original_image_iou_thresh = image_config.get("extract_original_image_iou_thresh", 0.9) if image_config else 0.9
    
    # 保存表格里的图片
    save_table_fill_image(
        page_model_info['layout_dets'],
        page_dict.get('table_fill_image_list', []),
        page_img_md5, page_index, image_writer
    )
    
    # 获取各类区块信息
    discarded_blocks = magic_model.get_discarded()
    text_blocks = magic_model.get_text_blocks()
    title_blocks = magic_model.get_title_blocks()
    inline_equations, interline_equations, interline_equation_blocks = magic_model.get_equations()
    formula_number_blocks = magic_model.get_formula_numbers()
    
    img_groups = magic_model.get_imgs()
    table_groups = magic_model.get_tables()
    
    # 对 image 和 table 的区块分组
    img_body_blocks, img_caption_blocks, img_footnote_blocks, maybe_text_image_blocks = process_groups(
        img_groups, 'image_body', 'image_caption_list', 'image_footnote_list'
    )
    
    table_body_blocks, table_caption_blocks, table_footnote_blocks, _ = process_groups(
        table_groups, 'table_body', 'table_caption_list', 'table_footnote_list'
    )
    
    # 获取所有 spans 信息
    spans = magic_model.get_all_spans()
    
    # 获取 VL OCR spans (如果启用)
    vl_ocr_spans = []
    if use_vl_ocr:
        vl_ocr_spans = magic_model.get_vl_ocr_spans()
    
    # 处理可能是文本块的图像
    if maybe_text_image_blocks:
        for block in maybe_text_image_blocks:
            img_body_blocks.append(block)
    
    # 处理行间公式
    if formula_enabled:
        interline_equation_blocks = []
    
    if interline_equation_blocks:
        for block in interline_equation_blocks:
            spans.append({
                "type": ContentType.INTERLINE_EQUATION,
                'score': block['score'],
                "bbox": block['bbox'],
                "content": "",
            })
    
    # 准备所有区块的 bbox
    if interline_equation_blocks:
        all_bboxes, all_discarded_blocks, footnote_blocks = prepare_block_bboxes(
            img_body_blocks, img_caption_blocks, img_footnote_blocks,
            table_body_blocks, table_caption_blocks, table_footnote_blocks,
            discarded_blocks, text_blocks, title_blocks,
            interline_equation_blocks, page_w, page_h,
            formula_number_blocks=formula_number_blocks,
        )
    else:
        all_bboxes, all_discarded_blocks, footnote_blocks = prepare_block_bboxes(
            img_body_blocks, img_caption_blocks, img_footnote_blocks,
            table_body_blocks, table_caption_blocks, table_footnote_blocks,
            discarded_blocks, text_blocks, title_blocks,
            interline_equations, page_w, page_h,
            formula_number_blocks=formula_number_blocks,
        )
    
    # 过滤 spans
    spans = remove_outside_spans(spans, all_bboxes, all_discarded_blocks)
    spans, _ = remove_overlaps_low_confidence_spans(spans)
    spans, _ = remove_overlaps_min_spans(spans) #  删除重叠spans中较小的那些
    
    # 根据 OCR 模式处理 spans
    if use_vl_ocr:
        # VL OCR 模式: 直接使用 VL OCR 的文本结果
        spans = _process_vl_ocr_spans(spans, vl_ocr_spans, all_bboxes, all_discarded_blocks)
    elif not ocr_enable:
        # 传统模式: 使用 PDF 文本提取
        spans = txt_spans_extract(
            page_dict, spans, page_pil_img, scale, all_bboxes, all_discarded_blocks
        )
    
    # 处理 discarded blocks
    discarded_block_with_spans, spans = fill_spans_in_blocks(all_discarded_blocks, spans, 0.4)
    fix_discarded_blocks = fix_discarded_block(discarded_block_with_spans)
    
    # 检查是否有有效的 bbox
    if len(all_bboxes) == 0 and len(fix_discarded_blocks) == 0:
        return None
    
    # 对 image/table/interline_equation 截图
    for span in spans:
        if span['type'] in [ContentType.IMAGE, ContentType.TABLE, ContentType.INTERLINE_EQUATION]:
            span = cut_image_and_table(
                span, page_dict['ori_image_list'],
                extract_original_image, extract_original_image_iou_thresh,
                page_pil_img, page_img_md5, page_index, image_writer, scale=scale
            )
    
    # span 填充进 block
    block_with_spans, spans = fill_spans_in_blocks(all_bboxes, spans, 0.5)
    
    # 对 block 进行 fix 操作
    fix_blocks = fix_block_spans(block_with_spans)
    
    # 对 block 进行排序
    sorted_blocks = sort_blocks_by_bbox(fix_blocks, page_w, page_h, footnote_blocks, page_pil_img)
    
    # 构造 page_info
    page_info = make_page_info_dict(sorted_blocks, page_index, page_w, page_h, fix_discarded_blocks)
    
    return page_info


def _process_vl_ocr_spans(spans, vl_ocr_spans, all_bboxes, all_discarded_blocks):
    """
    处理 VL OCR spans
    
    VL OCR 模式下，文本区域直接返回多行文本，
    将其作为整体 span 处理，保留区域级别的 bbox
    
    Args:
        spans: 原始 spans 列表
        vl_ocr_spans: VL OCR 识别的 spans
        all_bboxes: 所有区块 bbox
        all_discarded_blocks: 废弃区块
        
    Returns:
        处理后的 spans 列表
    """
    # 将 VL OCR spans 合并到普通 spans 中
    for vl_span in vl_ocr_spans:
        # VL OCR 的 span 已经包含完整的文本内容
        # 不需要进一步的 char 级别填充
        vl_span['score'] = vl_span.get('score', 0.95)
        vl_span['type'] = ContentType.TEXT
        spans.append(vl_span)
    
    return spans


def _extract_text_from_block(block):
    text_parts = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if span.get("type") == ContentType.TEXT:
                text_parts.append(span.get("content", ""))
    return "".join(text_parts).strip()


def _normalize_formula_tag_content(tag_content):
    tag_content = full_to_half(tag_content.strip())
    if tag_content.startswith("("):
        tag_content = tag_content[1:].strip()
    if tag_content.endswith(")"):
        tag_content = tag_content[:-1].strip()
    return tag_content


def _get_interline_equation_span(block):
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if span.get("type") == ContentType.INTERLINE_EQUATION:
                return span
    return None


def _append_formula_number_tag(equation_block, formula_number_block):
    equation_span = _get_interline_equation_span(equation_block)
    tag_content = _normalize_formula_tag_content(
        _extract_text_from_block(formula_number_block)
    )
    if equation_span is None or not tag_content:
        return False

    formula = equation_span.get("content", "")
    if not formula:
        return False

    equation_span["content"] = f"{formula}\\tag{{{tag_content}}}"
    return True


def _optimize_formula_number_blocks(pdf_info_list):
    for page_info in pdf_info_list:
        optimized_blocks = []
        blocks = page_info.get("preproc_blocks", [])
        for index, block in enumerate(blocks):
            if block.get("type") != BlockType.FORMULA_NUMBER:
                optimized_blocks.append(block)
                continue

            prev_block = blocks[index - 1] if index > 0 else None
            if (
                prev_block
                and prev_block.get("type") == BlockType.INTERLINE_EQUATION
                and _append_formula_number_tag(prev_block, block)
            ):
                continue

            next_block = blocks[index + 1] if index + 1 < len(blocks) else None
            next_next_block = blocks[index + 2] if index + 2 < len(blocks) else None
            if (
                next_block
                and next_block.get("type") == BlockType.INTERLINE_EQUATION
                and (
                    next_next_block is None
                    or next_next_block.get("type") != BlockType.FORMULA_NUMBER
                )
                and _append_formula_number_tag(next_block, block)
            ):
                continue

            if not _extract_text_from_block(block):
                continue

            block["type"] = BlockType.TEXT
            optimized_blocks.append(block)

        page_info["preproc_blocks"] = optimized_blocks


def _ensure_image_blocks(middle_json, page_dict_list, images_list, image_writer):
    """确定性图片保全（输出端兜底，para_split 前对 preproc_blocks 操作）。

    背景根因链（2026-08-20 三次部署排查结论）：
      a) 布局层 reinject 注入的 image block 到 middle_json 后 image_body 的
         lines/virtual_lines 均无 image span（span 对象不存在，非缺 image_path）；
      b) mkcontent 只读 image_body.lines[].spans[].image_path，不读 virtual_lines；
      c) result_to_middle_json 的 finally 会清空 ori_image_list 并关闭 img_pil，
         因此本函数必须在 try 块内（finally 之前）调用。

    两层保障：
      1) 补全：image_body 的 lines 中没有带 image_path 的 image span 时，
         用 image_body bbox 匹配 xref 原图（IoU>=0.5），构造完整 span，
         cut_image_and_table 落盘后写入 image_body["lines"]。
      2) 新增：对「未被任何 image span 覆盖、非表格内、面积>=
         RAPIDDOC_MIN_ORI_AREA_RATIO（默认 0.08）页面」的原图追加 image block。

    para_split 对 image 类 block 单独成组、原样保留，preproc_blocks 的补全
    会透传到 para_blocks 供 mkcontent 输出 ![](images/…)。
    """
    import math

    min_ratio = float(os.getenv('RAPIDDOC_MIN_ORI_AREA_RATIO', '0.08'))

    def _iou(a, b):
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        ua = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
        ub = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
        return inter / (ua + ub - inter) if (ua + ub - inter) > 0 else 0.0

    def _contain(inner, outer):
        x1, y1 = max(inner[0], outer[0]), max(inner[1], outer[1])
        x2, y2 = min(inner[2], outer[2]), min(inner[3], outer[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area = max(0, inner[2] - inner[0]) * max(0, inner[3] - inner[1])
        return inter / area if area > 0 else 0.0

    def _match_ori(bbox, ori_list):
        best, best_score = None, -1.0
        for o in ori_list:
            ob = o.get("bbox")
            if not ob or len(ob) < 4:
                continue
            score = _iou(bbox, ob)
            if score > best_score:
                best, best_score = o, score
        return best if best_score >= 0.5 else None

    def _cut_span(bbox, ori_list, page_pil_img, page_img_md5, page_idx, scale):
        span = {"type": ContentType.IMAGE, "bbox": list(bbox), "score": 1.0,
                "original_label": "image", "content": "", "image_path": ""}
        cut_image_and_table(span, ori_list, False, 0.3, page_pil_img,
                            page_img_md5, page_idx, image_writer, scale=scale)
        return span if span.get("image_path") else None

    for pidx, page_info in enumerate(middle_json.get("pdf_info", [])):
        if pidx >= len(page_dict_list) or pidx >= len(images_list):
            continue
        try:
            page_dict = page_dict_list[pidx]
            ori_list = page_dict.get("ori_image_list") or []
            if not ori_list:
                continue
            image_dict = images_list[pidx]
            scale = image_dict["scale"]
            page_pil_img = image_dict["img_pil"]
            page_img_md5 = bytes_md5(page_pil_img.tobytes())
            page_idx = page_info.get("page_idx", pidx)
            page_w, page_h = page_info.get("page_size", [0, 0])
            page_area = float(page_w) * float(page_h)

            blocks = page_info.setdefault("preproc_blocks", [])

            # 1) 补全：image_body 无带 image_path 的 image span -> 构造 span 写入 lines
            filled = 0
            for blk in blocks:
                if blk.get("type") != "image":
                    continue
                for sub in blk.get("blocks", []) or []:
                    if sub.get("type") != "image_body":
                        continue
                    has_path = any(
                        sp.get("type") == ContentType.IMAGE and sp.get("image_path")
                        for ln in sub.get("lines", []) or []
                        for sp in ln.get("spans", []) or [])
                    if has_path:
                        continue
                    target = _match_ori(sub.get("bbox") or blk.get("bbox"), ori_list)
                    if not target:
                        continue
                    span = _cut_span(target["bbox"], ori_list, page_pil_img,
                                     page_img_md5, page_idx, scale)
                    if not span:
                        continue
                    sub["lines"] = [{"bbox": list(target["bbox"]), "spans": [span]}]
                    filled += 1
            if filled:
                logger.info(f'_ensure_image_blocks: page {pidx} 补全 image span +{filled}')

            # 2) 新增：完全无 image span 覆盖的大图
            have = []
            for blk in blocks:
                if blk.get("type") == "table":
                    continue
                for sub in blk.get("blocks", []) or []:
                    for ln in sub.get("lines", []) or []:
                        for sp in ln.get("spans", []) or []:
                            if sp.get("type") == ContentType.IMAGE and sp.get("image_path"):
                                have.append(sp.get("bbox"))
            tables = [blk.get("bbox") for blk in blocks if blk.get("type") == "table"]
            appended = []
            for ori in ori_list:
                ob = ori.get("bbox")
                if not ob or len(ob) < 4:
                    continue
                ob_area = max(0, ob[2] - ob[0]) * max(0, ob[3] - ob[1])
                if page_area <= 0 or ob_area < min_ratio * page_area:
                    continue
                if any(_iou(ob, h) >= 0.3 for h in have if h):
                    continue
                if any(_contain(ob, t) >= 0.8 for t in tables if t):
                    continue
                span = _cut_span(ob, ori_list, page_pil_img, page_img_md5, page_idx, scale)
                if not span:
                    continue
                have.append(span["bbox"])
                appended.append(span)
            if appended:
                index = max([b.get("index", 0) for b in blocks] or [-1]) + 1
                for span in appended:
                    bbox = [math.floor(v * 100) / 100 for v in span["bbox"]]
                    pts = [[bbox[0], bbox[1]], [bbox[0], bbox[3]],
                           [bbox[2], bbox[3]], [bbox[2], bbox[1]]]
                    blocks.append({"type": "image", "bbox": bbox, "index": index,
                                   "original_order": index,
                                   "blocks": [{"type": "image_body", "bbox": bbox, "index": 0,
                                               "group_id": 0, "original_label": "image",
                                               "original_order": index,
                                               "polygon_points": pts,
                                               "lines": [{"bbox": bbox, "spans": [span]}],
                                               "virtual_lines": []}],
                                   "polygon_points": pts})
                    index += 1
                logger.info(f'_ensure_image_blocks: page {pidx} 新增 image block +{len(appended)}')
        except Exception as e:
            logger.warning(f'_ensure_image_blocks page {pidx} failed: {e}')


def result_to_middle_json(
    model_list,
    images_list,
    page_dict_list,
    image_writer,
    lang=None,
    ocr_enable=False,
    formula_enabled=True,
    ocr_config=None,
    image_config=None,
    batch_idx=0, pdf_pages_batch=0
):
    """
    将模型输出转换为中间 JSON 格式
    
    Args:
        model_list: 模型输出列表
        images_list: 图像列表
        page_dict_list: PDF 页面字典列表
        image_writer: 图像写入器
        lang: 语言
        ocr_enable: 是否启用 OCR
        formula_enabled: 是否启用公式识别
        ocr_config: OCR 配置
        image_config: 图像配置
        
    Returns:
        中间 JSON 格式数据
    """
    middle_json = {
        "pdf_info": [],
        "_backend": "pipeline",
        "_version_name": __version__
    }
    
    formula_enabled = get_formula_enable(formula_enabled)
    
    # 检查是否使用 VL OCR
    atom_model_manager = AtomModelSingleton()
    ocr_model = atom_model_manager.get_atom_model(
        atom_model_name='ocr',
        lang=lang,
        ocr_config=ocr_config,
    )
    use_vl_ocr = isinstance(ocr_model, CustomBaseModel)
    
    try:
        for page_index, page_model_info in tqdm(enumerate(model_list), total=len(model_list), desc="Processing pages"):
            page_dict = page_dict_list[page_index]
            image_dict = images_list[page_index]

            page_info = page_model_info_to_page_info(
                page_model_info, image_dict, page_dict, image_writer, page_index + batch_idx * pdf_pages_batch,
                ocr_enable=ocr_enable, formula_enabled=formula_enabled,
                image_config=image_config, use_vl_ocr=use_vl_ocr
            )

            if page_info is None:
                page_w, page_h = map(int, page_dict['size'])
                page_info = make_page_info_dict([], page_index + batch_idx * pdf_pages_batch, page_w, page_h, [])

            middle_json["pdf_info"].append(page_info)

        # 输出端兜底（必须在 finally 前：此时 ori_image_list 未清空、img_pil 未关闭）
        # 对 preproc_blocks 补全/新增 image span；para_split 对 image block 单独成组会原样保留
        try:
            _ensure_image_blocks(middle_json, page_dict_list, images_list, image_writer)
        except Exception as e:
            logger.warning(f'_ensure_image_blocks failed: {e}')
    finally:
        # 页面信息生成后，原始页面图和 PDF 内嵌图都不再参与后处理。
        for image_dict in images_list:
            pil_image = image_dict.get('img_pil')
            if pil_image is not None:
                try:
                    pil_image.close()
                except Exception:
                    pass
        for page_dict in page_dict_list:
            for image_dict in page_dict.get('ori_image_list', []):
                pil_image = image_dict.get('pil_image')
                if pil_image is not None:
                    try:
                        pil_image.close()
                    except Exception:
                        pass
            page_dict['ori_image_list'] = []
    
    # 后置 OCR 处理 (仅在非 VL OCR 模式下)
    if not use_vl_ocr:
        _post_process_ocr(middle_json, lang, ocr_config)
    
    _optimize_formula_number_blocks(middle_json["pdf_info"])
    
    # 分段处理
    para_split(middle_json["pdf_info"])

    # 表格跨页合并
    cross_page_table_merge(middle_json["pdf_info"])
    
    # 清理内存
    if os.getenv('MINERU_DONOT_CLEAN_MEM') is None:
        clean_memory(get_device())
    
    return middle_json


def _post_process_ocr(middle_json, lang, ocr_config):
    """后置 OCR 处理"""
    need_ocr_list = []
    img_crop_list = []
    text_block_list = []
    
    for page_info in middle_json["pdf_info"]:
        for block in page_info['preproc_blocks']:
            if block['type'] in ['table', 'image']:
                for sub_block in block['blocks']:
                    if sub_block['type'] in ['image_caption', 'image_footnote', 'table_caption', 'table_footnote']:
                        text_block_list.append(sub_block)
            elif block['type'] in ['text', 'title', 'formula_number']:
                text_block_list.append(block)
        
        for block in page_info['discarded_blocks']:
            text_block_list.append(block)
    
    for block in text_block_list:
        for line in block['lines']:
            for span in line['spans']:
                if 'np_img' in span:
                    need_ocr_list.append(span)
                    img_crop_list.append(span['np_img'])
                    span.pop('np_img')
    
    if img_crop_list:
        atom_model_manager = AtomModelSingleton()
        ocr_model = atom_model_manager.get_atom_model(
            atom_model_name='ocr',
            lang=lang,
            ocr_config=ocr_config,
        )
        
        ocr_res_list = ocr_model.ocr(img_crop_list, det=False, tqdm_enable=True)[0]
        
        assert len(ocr_res_list) == len(need_ocr_list), \
            f'ocr_res_list: {len(ocr_res_list)}, need_ocr_list: {len(need_ocr_list)}'
        
        for span, (ocr_text, ocr_score) in zip(need_ocr_list, ocr_res_list):
            if ocr_score > OcrConfidence.min_confidence:
                span['content'] = ocr_text
                span['score'] = float(f"{ocr_score:.3f}")
            else:
                span['content'] = ''
                span['score'] = 0.0


def make_page_info_dict(blocks, page_id, page_w, page_h, discarded_blocks):
    """构造页面信息字典"""
    return {
        'preproc_blocks': blocks,
        'page_idx': page_id,
        'page_size': [page_w, page_h],
        'discarded_blocks': discarded_blocks,
    }
