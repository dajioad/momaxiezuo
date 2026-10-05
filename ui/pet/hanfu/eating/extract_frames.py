# -*- coding: utf-8 -*-
"""从 mp4 拆帧 + 连通域背景抠图，输出 frame_NN.png。

原理：
1. 采样背景色，标记所有"接近背景色"的像素
2. 用连通域分析：只保留"和画布边缘连通"的连通块作为背景
3. 角色内部的白色（被线稿包围，不连通到边缘）不被误抠

这个方案能处理"背景和角色颜色接近"的场景。
"""
import cv2
import numpy as np
import os

# ============ 可调参数 ============
VIDEO = "eating.mp4"
OUT_DIR = "."
FPS_TARGET = 5
TARGET_SIZE = 512

# 判定"接近背景"的距离阈值。这里可以放宽一些，
# 因为连通域会过滤掉角色内部的误判。
DISTANCE_THRESHOLD = 35

# 羽化宽度
FEATHER = 21
# ==================================


def sample_bg_color(frame):
    h, w = frame.shape[:2]
    patch = 30
    corners = [
        frame[0:patch, 0:patch],
        frame[0:patch, w - patch:w],
        frame[h - patch:h, 0:patch],
        frame[h - patch:h, w - patch:w],
    ]
    samples = [c.reshape(-1, 3).astype(float).mean(axis=0)
               for c in corners]
    return np.mean(samples, axis=0)


def extract_bg_mask(frame, bg, threshold):
    """连通域抠图：只抠和边缘连通的白块。

    返回 0-255 的 alpha 图（255 = 前景，0 = 背景）。
    """
    # 1. 距离图
    diff = frame.astype(float) - bg
    dist = np.sqrt((diff ** 2).sum(axis=2))

    # 2. 可能是背景的像素（距离小）
    candidate = (dist < threshold).astype(np.uint8)

    # 3. 连通域分析
    # 只保留和画布四边连通的连通块
    num, labels = cv2.connectedComponents(candidate, connectivity=4)

    # 收集所有"触及边缘"的标签
    h, w = candidate.shape
    edge_labels = set()
    edge_labels.update(labels[0, :].tolist())       # 上
    edge_labels.update(labels[-1, :].tolist())      # 下
    edge_labels.update(labels[:, 0].tolist())       # 左
    edge_labels.update(labels[:, -1].tolist())      # 右
    edge_labels.discard(0)  # 0 是背景标签（不属于任何连通块）

    # 4. 生成背景 mask（和边缘连通的才算背景）
    bg_mask = np.zeros_like(candidate, dtype=np.uint8)
    for lab in edge_labels:
        bg_mask[labels == lab] = 255

    # 5. alpha = 反相 + 羽化
    alpha = 255 - bg_mask
    if FEATHER > 1:
        alpha = cv2.GaussianBlur(
            alpha, (FEATHER | 1, FEATHER | 1), 0)
    return alpha


def main():
    if not os.path.exists(VIDEO):
        print(f"找不到视频：{VIDEO}")
        return

    cap = cv2.VideoCapture(VIDEO)
    if not cap.isOpened():
        print("无法打开视频")
        return

    ret, first = cap.read()
    if not ret:
        print("无法读取第一帧")
        return
    bg = sample_bg_color(first)
    print(f"采样背景色 BGR = ({bg[0]:.0f}, {bg[1]:.0f}, {bg[2]:.0f})")

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    src_fps = cap.get(cv2.CAP_PROP_FPS)
    interval = max(1, int(round(src_fps / FPS_TARGET)))
    print(f"源视频 {src_fps:.1f} fps，每 {interval} 帧取一帧")

    idx = 0
    saved = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % interval != 0:
            idx += 1
            continue

        h, w = frame.shape[:2]
        scale = TARGET_SIZE / max(w, h)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))
        frame = cv2.resize(frame, (new_w, new_h),
                           interpolation=cv2.INTER_AREA)

        # 连通域抠图
        alpha = extract_bg_mask(frame, bg, DISTANCE_THRESHOLD)

        bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = alpha

        canvas = np.zeros((TARGET_SIZE, TARGET_SIZE, 4), dtype=np.uint8)
        y_off = (TARGET_SIZE - new_h) // 2
        x_off = (TARGET_SIZE - new_w) // 2
        canvas[y_off:y_off + new_h, x_off:x_off + new_w] = bgra

        out_path = os.path.join(OUT_DIR, f"frame_{saved:02d}.png")
        cv2.imwrite(out_path, canvas)
        print(f"  {out_path}")
        saved += 1
        idx += 1

    cap.release()
    print(f"\n共提取 {saved} 帧")


if __name__ == "__main__":
    main()