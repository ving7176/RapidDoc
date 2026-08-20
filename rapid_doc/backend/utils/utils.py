import os
from copy import deepcopy

from loguru import logger
from typing import List, Tuple, Dict, Optional
from rapid_doc.utils.table_merge import merge_table
from rapid_doc.model.layout.rapid_layout_self.model_handler.pp_doclayout.post_process import calculate_polygon_overlap_ratio, \
    calculate_bbox_area
from rapid_doc.model.reading_order.utils import calculate_overlap_ratio
from rapid_doc.utils.boxbase import is_in
from rapid_doc.utils.span_pre_proc import txt_in_ori_image


def cross_page_table_merge(pdf_info: list[dict]):
    """Merge tables that span across multiple pages in a PDF document.

    Args:
        pdf_info (list[dict]): A list of dictionaries containing information about each page in the PDF.

    Returns:
        None
    """
    is_merge_table = os.getenv('MINERU_TABLE_MERGE_ENABLE', 'true')
    if is_merge_table.lower() in ['true', '1', 'yes']:
        merge_table(pdf_info)
    elif is_merge_table.lower() in ['false', '0', 'no']:
        pass
    else:
        logger.warning(f'unknown MINERU_TABLE_MERGE_ENABLE config: {is_merge_table}, pass')
        pass

def _bbox_iou(a, b):
    """两 bbox 的 IoU。"""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _containment(inner, outer):
    """inner 被 outer 覆盖的面积占比。"""
    x1, y1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x2, y2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = max(0, inner[2] - inner[0]) * max(0, inner[3] - inner[1])
    return inter / area if area > 0 else 0.0


def reinject_uncovered_ori_images(images_layout_res, pdf_dict_list, scale_list,
                                  min_area_ratio=0.1, cover_iou=0.3):
    """确定性图片回注兜底（与布局模型无关）。

    对每页的 xref 原图，若布局结果中没有任何 image 区域覆盖它
    （IoU >= cover_iou），且它未被 table 区域大面积覆盖、面积占比
    >= min_area_ratio（过滤 logo/横幅等小图），则强制注入 image 检测块。

    与 remove_layout_in_ori_images 的关键差异：不跳过含文字的图片——
    截图型操作手册的截图必然含文字，此前的 txt_in_ori_image 过滤正是
    这类文档系统性丢图的根因。

    环境开关：RAPIDDOC_REINJECT_ORI_IMAGE=false 可关闭（默认开启）。
    """
    if os.getenv('RAPIDDOC_REINJECT_ORI_IMAGE', 'true').lower() in ('false', '0', 'no'):
        return images_layout_res

    for index, layout_res in enumerate(images_layout_res):
        ori_image_list = pdf_dict_list[index].get('ori_image_list')
        scale = scale_list[index]
        page_w = pdf_dict_list[index].get('page_info', {}).get('w', 0)
        page_h = pdf_dict_list[index].get('page_info', {}).get('h', 0)
        page_area = float(page_w) * float(page_h)
        if not ori_image_list or page_area <= 0:
            continue

        # 布局结果 bbox：category_id==3 为图片，==2 为表格
        image_bboxes = []
        table_bboxes = []
        for res in layout_res:
            p = res.get('poly') or []
            if len(p) < 8:
                continue
            bbox = [p[0], p[1], p[4], p[5]]
            if res.get('category_id') == 3:
                image_bboxes.append(bbox)
            elif res.get('category_id') == 2:
                table_bboxes.append(bbox)

        injected = 0
        for ori in ori_image_list:
            ob = ori.get('bbox')
            if not ob or len(ob) < 4:
                continue
            # 与 remove_layout_in_ori_images 相同约定：布局 bbox 为缩放后坐标
            ob = [v * scale for v in ob[:4]]
            ob_area = max(0, ob[2] - ob[0]) * max(0, ob[3] - ob[1])
            if ob_area < min_area_ratio * page_area * scale * scale:
                continue  # 小图/logo/窄横幅
            if any(_bbox_iou(ob, ib) >= cover_iou for ib in image_bboxes):
                continue  # 已有图片区域覆盖
            if any(_containment(ob, tb) >= 0.8 for tb in table_bboxes):
                continue  # 表格区域（表格会单独截图）
            xmin, ymin, xmax, ymax = [int(v) for v in ob]
            layout_res.append({
                "category_id": 3,
                "original_label": "image",
                "poly": [xmin, ymin, xmax, ymin, xmax, ymax, xmin, ymax],
                "score": 1.0,
            })
            injected += 1
        if injected:
            logger.info(f'reinject_uncovered_ori_images: page {index} +{injected} image(s)')
    return images_layout_res


def remove_layout_in_ori_images(images_layout_res, pdf_dict_list, scale_list):
    """
    移除落在原始图片区域内的 layout_res，
    对于确实替换掉内容的图片区域，添加一条新的 'image' 类型检测结果。
    若图片区域中含有文本（背景图），则跳过该图片。
    """
    for index, layout_res in enumerate(images_layout_res):
        ori_image_list = pdf_dict_list[index].get('ori_image_list')
        scale = scale_list[index]

        if not ori_image_list:
            continue

        # ✅ 过滤掉包含文本的“背景图”区域
        valid_ori_images = []
        for ori in ori_image_list:
            if txt_in_ori_image(pdf_dict_list[index], ori['bbox']):
                # 含文本，不视为纯图片，跳过
                continue
            valid_ori_images.append(ori)

        if not valid_ori_images:
            # 没有纯图片，跳过
            images_layout_res[index] = layout_res
            continue

        # ✅ 计算所有原图 bbox（已缩放）
        scaled_ori_bboxes = [
            [
                ori['bbox'][0] * scale,
                ori['bbox'][1] * scale,
                ori['bbox'][2] * scale,
                ori['bbox'][3] * scale
            ]
            for ori in valid_ori_images
        ]

        filtered_layout_res = []
        replaced_ori_bboxes = set()  # 记录被用来替换的图片区域下标

        for res in layout_res:
            # 保留 category_id==2 的区域
            if res['category_id'] == 2:
                filtered_layout_res.append(res)
                continue

            x1, y1, x2, y2 = res['poly'][0], res['poly'][1], res['poly'][4], res['poly'][5]
            res_bbox = [int(x1), int(y1), int(x2), int(y2)]

            # 检查是否落入某个图片区域
            matched_idx = None
            for idx, ori_bbox in enumerate(scaled_ori_bboxes):
                if is_in(res_bbox, ori_bbox):
                    matched_idx = idx
                    break

            if matched_idx is not None:
                replaced_ori_bboxes.add(matched_idx)  # 记录该图片区域确实发生替换
                continue  # 删除该 layout_res
            filtered_layout_res.append(res)

        # ✅ 只添加被替换掉的图片区域
        for idx in replaced_ori_bboxes:
            xmin, ymin, xmax, ymax = map(int, scaled_ori_bboxes[idx])
            image_res = {
                "category_id": 3,
                "original_label": "image",
                "poly": [xmin, ymin, xmax, ymin, xmax, ymax, xmin, ymax],
                "score": 1.0,
            }
            filtered_layout_res.append(image_res)

        images_layout_res[index] = filtered_layout_res

    return images_layout_res


def filter_overlap_boxes(layout_det_res, use_custom_ocr):
    """
    Remove overlapping boxes from layout detection results based on a given overlap ratio.

    Args:
        layout_det_res: Layout detection result dict containing a 'boxes' list.

    Returns:
       Filtered dict with overlapping boxes removed.
    """
    layout_det_res_filtered = deepcopy(layout_det_res)
    boxes = [
        box for box in layout_det_res_filtered if box["original_label"] != "reference"
    ]
    dropped_indexes = set()

    for i in range(len(boxes)):
        coordinate_i = [boxes[i]["poly"][0], boxes[i]["poly"][1], boxes[i]["poly"][4], boxes[i]["poly"][5]]
        x1, y1, x2, y2 = coordinate_i
        w, h = x2 - x1, y2 - y1
        if w < 6 or h < 6:
            dropped_indexes.add(i)
        for j in range(i + 1, len(boxes)):
            if i in dropped_indexes or j in dropped_indexes:
                continue
            coordinate_j = [boxes[j]["poly"][0], boxes[j]["poly"][1], boxes[j]["poly"][4], boxes[j]["poly"][5]]
            overlap_ratio = calculate_overlap_ratio(
                coordinate_i, coordinate_j, "small"
            )
            if (
                boxes[i]["original_label"] == "inline_formula"
                or boxes[j]["original_label"] == "inline_formula"
            ):
                if not use_custom_ocr:
                    continue
                if overlap_ratio > 0.5:
                    if boxes[i]["original_label"] == "inline_formula":
                        dropped_indexes.add(i)
                    if boxes[j]["original_label"] == "inline_formula":
                        dropped_indexes.add(j)
                    continue
            if overlap_ratio > 0.7:
                if boxes[i].get("polygon_points"):
                    poly_overlap_ratio = calculate_polygon_overlap_ratio(
                        boxes[i]["polygon_points"], boxes[j]["polygon_points"], "small"
                    )
                    if poly_overlap_ratio < 0.7:
                        continue
                box_area_i = calculate_bbox_area(coordinate_i)
                box_area_j = calculate_bbox_area(coordinate_j)
                if {boxes[i]["original_label"], boxes[j]["original_label"]} & {
                    "image",
                    # "table",
                    "seal",
                    "chart",
                } and boxes[i]["original_label"] != boxes[j]["original_label"]:
                    continue
                if box_area_i >= box_area_j:
                    dropped_indexes.add(j)
                else:
                    dropped_indexes.add(i)
    layout_det_res_filtered = [
        box for idx, box in enumerate(boxes) if idx not in dropped_indexes
    ]
    return layout_det_res_filtered

def _rect_from_poly(res: Dict) -> Optional[Tuple[int, int, int, int]]:
    poly = res.get("poly")
    if not poly or len(poly) < 6:
        return None

    xs = [int(round(float(poly[i]))) for i in range(0, len(poly), 2)]
    ys = [int(round(float(poly[i]))) for i in range(1, len(poly), 2)]
    return min(xs), min(ys), max(xs), max(ys)


def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)


def _expand_formula_crop_res(
    formula_res: Dict,
    layout_res: List[Dict],
    image_shape: Tuple[int, ...],
    expand_px: int,
) -> Dict:
    if expand_px <= 0:
        return formula_res

    rect = _rect_from_poly(formula_res)
    if rect is None:
        return formula_res

    image_h, image_w = image_shape[:2]
    x0, y0, x1, y1 = rect
    expanded_x0 = max(0, x0 - expand_px)
    expanded_y0 = max(0, y0 - expand_px)
    expanded_x1 = min(image_w, x1 + expand_px)
    expanded_y1 = min(image_h, y1 + expand_px)

    for other_res in layout_res:
        if other_res is formula_res:
            continue

        other_rect = _rect_from_poly(other_res)
        if other_rect is None:
            continue

        ox0, oy0, ox1, oy1 = other_rect

        if ox1 <= x0 and _ranges_overlap(expanded_y0, expanded_y1, oy0, oy1):
            expanded_x0 = max(expanded_x0, ox1)
        if ox0 >= x1 and _ranges_overlap(expanded_y0, expanded_y1, oy0, oy1):
            expanded_x1 = min(expanded_x1, ox0)
        if oy1 <= y0 and _ranges_overlap(expanded_x0, expanded_x1, ox0, ox1):
            expanded_y0 = max(expanded_y0, oy1)
        if oy0 >= y1 and _ranges_overlap(expanded_x0, expanded_x1, ox0, ox1):
            expanded_y1 = min(expanded_y1, oy0)

    if expanded_x0 >= expanded_x1 or expanded_y0 >= expanded_y1:
        return formula_res

    crop_res = formula_res.copy()
    crop_res["poly"] = [
        expanded_x0, expanded_y0,
        expanded_x1, expanded_y0,
        expanded_x1, expanded_y1,
        expanded_x0, expanded_y1,
    ]
    crop_res["bbox"] = [expanded_x0, expanded_y0, expanded_x1, expanded_y1]
    crop_res.pop("polygon_points", None)
    return crop_res