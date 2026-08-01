# -*- coding: utf-8 -*-
"""
Шаг 5. Из смысловой карты — в координаты значков на реальном рабочем столе.

Раскладываем только по рабочей области ОСНОВНОГО монитора: ListView стола
общий на все экраны, и без явного ограничения значки уезжают на соседние.

Карта квадратная и в [0..1], а область монитора — прямоугольник.
Просто растянуть квадрат в полосу — значит соврать про расстояния, поэтому
облако точек сначала поворачивается по главным осям (PCA), чтобы его самая
длинная ось легла вдоль ширины, и только потом масштабируется.
Коэффициент искажения печатается — видно, чем платим.

Если значков больше, чем ячеек влезает, сначала ужми сетку:
    python desktop_icons.py spacing 88 116

Два режима:
  --grid  (по умолчанию) значки садятся в ячейки сетки Windows, коллизии
          разруливаются спиральным поиском ближайшей свободной ячейки;
  --free  произвольные пиксельные координаты с расталкиванием (это то самое
          "не по сетке"; требует выключенного «выровнять по сетке»).

    python layout_desktop.py описание --grid
    python layout_desktop.py гибрид --free
Затем:
    python desktop_icons.py apply data/layout.json
"""

import os
import sys
import json
import math

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

MARGIN = 24            # отступ от краёв экрана, px
ICON_W, ICON_H = 100, 128   # примерный габарит значка с подписью


def pca_align(P):
    """Поворачивает облако так, чтобы его главная ось стала горизонталью."""
    C = P - P.mean(axis=0)
    cov = np.cov(C.T)
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]           # первая компонента — самая длинная
    R = vecs[:, order]
    if np.linalg.det(R) < 0:                 # без зеркала — карту не переворачиваем
        R[:, 1] *= -1
    return C @ R


def fit_to_area(P, w, h):
    """Растягивает в прямоугольник w x h. Возвращает координаты и коэффициент искажения."""
    span = P.max(axis=0) - P.min(axis=0)
    span[span == 0] = 1.0
    Q = (P - P.min(axis=0)) / span
    sx, sy = w, h
    aspect_src = span[0] / span[1]
    aspect_dst = w / h
    distortion = aspect_dst / aspect_src
    return np.column_stack([Q[:, 0] * sx, Q[:, 1] * sy]), distortion


def assign_cells(XY, cells):
    """
    Кто в какую ячейку — оптимально, а не «кто первый успел».

    Значков почти столько же, сколько ячеек, поэтому жадный подход («сядь в свою,
    занято — ищи ближайшую свободную») разваливается: первые занимают лучшие места,
    последние улетают через весь экран, и смысловая структура теряется.
    Венгерский алгоритм минимизирует СУММУ смещений по всем значкам сразу —
    каждый чуть подвинется, но никто не улетит.

    XY   — желаемые координаты (n, 2)
    cells— координаты свободных ячеек (m, 2), m >= n
    Возвращает список индексов ячеек для каждого значка.
    """
    from scipy.optimize import linear_sum_assignment
    from scipy.spatial.distance import cdist

    cost = cdist(XY, cells, metric="sqeuclidean")
    rows, cols = linear_sum_assignment(cost)
    out = [0] * len(XY)
    for r, c in zip(rows, cols):
        out[r] = c
    return out


def grid_origin(geom, positions):
    """
    Первый узел сетки Windows внутри рабочей области основного монитора.

    Со включённым «выровнять по сетке» система всё равно округлит наши
    координаты до своих узлов, поэтому узлы надо угадать, а не назначить:
    берём самый частый остаток от деления текущих координат на шаг сетки
    и находим первый такой узел правее/ниже начала монитора.
    """
    from collections import Counter
    cw, ch = geom["cell_w"], geom["cell_h"]
    if positions:
        rx = Counter(x % cw for x, _ in positions).most_common(1)[0][0]
        ry = Counter(y % ch for _, y in positions).most_common(1)[0][0]
    else:
        rx, ry = geom["area_x"] % cw, geom["area_y"] % ch

    ox = geom["area_x"] + ((rx - geom["area_x"]) % cw)
    oy = geom["area_y"] + ((ry - geom["area_y"]) % ch)
    return ox, oy


def layout_grid(names, P, geom, reserved, fill=False, slack=1.6, origin=None):
    """
    Ширина области — компромисс.

    Занять весь экран (--fill) значит растянуть квадратную карту в полосу 4:1:
    соседи по вертикали слипнутся, по горизонтали разъедутся. Поэтому по
    умолчанию берём столько колонок, чтобы ячеек хватило с запасом `slack`,
    но область осталась как можно ближе к квадрату — искажение падает в разы,
    ценой незанятого края экрана.
    """
    cw, ch = geom["cell_w"], geom["cell_h"]
    ox, oy = origin
    # сколько ячеек влезает от начала до правого/нижнего края основного монитора
    right = geom["area_x"] + geom["area_w"]
    bottom = geom["area_y"] + geom["area_h"]
    cols_max = max(1, (right - ox) // cw)
    rows = max(1, (bottom - oy) // ch)
    print(f"  начало сетки: ({ox},{oy}), шаг {cw}x{ch}, "
          f"монитор до ({right},{bottom})")

    n_need = len(names) + len(reserved)
    if fill:
        cols = cols_max
    else:
        cols_min = max(1, math.ceil(n_need * slack / rows))
        cols = min(cols_max, cols_min)
        if cols * rows < n_need:
            print(f"  !! при {rows} рядах нужно минимум {math.ceil(n_need/rows)} колонок")
            cols = min(cols_max, math.ceil(n_need / rows))

    used_aspect = (cols * cw) / (rows * ch)
    print(f"  сетка: {cols}x{rows} = {cols*rows} ячеек на {len(names)} значков "
          f"(экран даёт до {cols_max} колонок)")
    print(f"  пропорции области: {used_aspect:.2f}:1")
    if cols * rows < n_need:
        print("  !! ячеек меньше, чем значков — часть уедет за край")

    XY, dist = fit_to_area(P, (cols - 1) * cw, (rows - 1) * ch)
    print(f"  искажение пропорций: x{dist:.2f}")

    # системные значки (Корзина и т.п.) мы не двигаем — значит их клетки заняты
    taken = set()
    for _, (rx, ry) in reserved.items():
        taken.add((int(round((rx - ox) / cw)), int(round((ry - oy) / ch))))

    free = [(c, r) for r in range(rows) for c in range(cols) if (c, r) not in taken]
    if len(free) < len(names):
        print(f"  !! свободных ячеек {len(free)}, а значков {len(names)}")
        free += [(c, rows + i) for i, c in enumerate(range(len(names) - len(free)))]

    cells = np.array([[c * cw, r * ch] for c, r in free], dtype=np.float64)
    pick = assign_cells(XY, cells)

    shift = np.linalg.norm(XY - cells[pick], axis=1)
    print(f"  смещение от идеала: медиана {np.median(shift):.0f}px, "
          f"максимум {shift.max():.0f}px, заполнено {len(names)}/{len(free)} ячеек")

    return {names[i]: (ox + free[pick[i]][0] * cw, oy + free[pick[i]][1] * ch)
            for i in range(len(names))}


def layout_free(names, P, geom, reserved):
    """Свободные координаты + расталкивание, чтобы значки не налезали друг на друга."""
    ox, oy = geom["area_x"] + MARGIN, geom["area_y"] + MARGIN
    w = geom["area_w"] - 2 * MARGIN - ICON_W
    h = geom["area_h"] - 2 * MARGIN - ICON_H
    XY, dist = fit_to_area(P, w, h)
    print(f"  искажение пропорций: x{dist:.2f}")

    XY = XY.astype(np.float64)
    # неподвижные препятствия — системные значки, в тех же координатах, что и XY
    fixed = np.array([[x - ox, y - oy] for x, y in reserved.values()],
                     dtype=np.float64) if reserved else np.zeros((0, 2))
    min_dx, min_dy = ICON_W * 0.92, ICON_H * 0.80

    n_over = 0
    for _ in range(220):                      # простая релаксация перекрытий
        shift = np.zeros_like(XY)
        n_over = 0
        others = np.vstack([XY, fixed])
        for i in range(len(XY)):
            d = others - XY[i]
            ox = min_dx - np.abs(d[:, 0])
            oy = min_dy - np.abs(d[:, 1])
            bad = (ox > 0) & (oy > 0)
            bad[i] = False
            if not bad.any():
                continue
            n_over += 1
            for j in np.nonzero(bad)[0]:
                dx, dy = d[j]
                # расталкиваем по той оси, где перекрытие меньше — так меньше врём
                if ox[j] / min_dx < oy[j] / min_dy:
                    shift[i, 0] -= math.copysign(ox[j] * 0.5, dx if dx else 1.0)
                else:
                    shift[i, 1] -= math.copysign(oy[j] * 0.5, dy if dy else 1.0)
        if not n_over:
            break
        XY += shift * 0.5
        XY[:, 0] = np.clip(XY[:, 0], 0, w)
        XY[:, 1] = np.clip(XY[:, 1], 0, h)

    print(f"  осталось перекрытий: {n_over}")
    return {names[i]: (int(ox + XY[i, 0]), int(oy + XY[i, 1]))
            for i in range(len(names))}


def main():
    variant = sys.argv[1] if len(sys.argv) > 1 else "гибрид"
    mode = "free" if "--free" in sys.argv else "grid"

    MAP = json.load(open(os.path.join(DATA, "map.json"), encoding="utf-8"))
    if variant not in MAP["variants"]:
        print(f"Нет варианта '{variant}'. Есть: {', '.join(MAP['variants'])}")
        return

    sys.path.insert(0, HERE)
    from desktop_icons import find_desktop_listview, get_state, read_icons

    defview, lv = find_desktop_listview()
    geom = get_state(defview, lv)
    on_desktop = {n: (x, y) for _, n, x, y in read_icons(lv)}

    pts = MAP["variants"][variant]["points"]

    # Проводник может показывать имена без расширений, а в именах бывают точки
    # ("LibreOffice 26.2"), поэтому сверяем и полное имя, и имя без расширения —
    # с обеих сторон.
    def keys(names_iter):
        s = set()
        for x in names_iter:
            s.add(x)
            s.add(os.path.splitext(x)[0])
        return s

    map_keys = keys(pts)
    desk_keys = keys(on_desktop)
    same = lambda x, ks: x in ks or os.path.splitext(x)[0] in ks

    # значки, которых нет в карте (Корзина и прочее системное) — их не двигаем
    reserved = {n: xy for n, xy in on_desktop.items() if not same(n, map_keys)}

    names, coords = [], []
    for n, p in pts.items():
        if same(n, desk_keys):
            names.append(n)
            coords.append((p["x"], p["y"]))
    print(f"Вариант '{variant}', режим {mode}")
    print(f"  из карты на столе найдено: {len(names)}; не двигаем: {len(reserved)} "
          f"({', '.join(reserved) if reserved else '—'})")

    P = pca_align(np.array(coords, dtype=np.float64))

    print(f"  основной монитор: {geom['area_w']}x{geom['area_h']} "
          f"(весь стол {geom['width']}x{geom['height']})")

    if mode == "grid":
        placed = layout_grid(names, P, geom, reserved, fill="--fill" in sys.argv,
                             origin=grid_origin(geom, list(on_desktop.values())))
    else:
        if geom["snap_to_grid"]:
            print("  !! включено «выровнять значки по сетке» — свободные координаты")
            print("     округлятся Windows. Выключить: python desktop_icons.py freeform on")
        placed = layout_free(names, P, geom, reserved)

    out = {
        "variant": variant,
        "mode": mode,
        "geometry": geom,
        "positions": [{"name": n, "x": xy[0], "y": xy[1]} for n, xy in placed.items()],
    }
    path = os.path.join(DATA, "layout.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n-> {path}")
    print("Применить:  python desktop_icons.py apply data/layout.json")


if __name__ == "__main__":
    main()
