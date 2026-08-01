# -*- coding: utf-8 -*-
"""
Шаг 2. Смысловые координаты рабочего стола.

Три варианта карты, чтобы сравнить подходы (то самое "и то и то"):
  A "описание"  — эмбеддинг человекочитаемого описания элемента
  B "содержимое"— эмбеддинг сырого содержимого (дерево файлов + куски кода/текста)
  C "гибрид"    — среднее нормированных векторов A и B

Пайплайн одного варианта:
  текст -> чанки -> LaBSE(normalize=True) -> среднее -> L2 -> KMeans -> UMAP(cosine)

Почему так, а не как в соц аналитике:
  * эмбеддинги нормируются ДО усреднения (иначе длинные векторы перетягивают центр);
  * KMeans работает на L2-нормированных векторах, поэтому его евклидова метрика
    согласована с косинусной метрикой UMAP — кластеры совпадают с картинкой;
  * silhouette считается тоже по cosine;
  * имена кластерам даются по TF-IDF самих элементов, а не подбираются
    из фиксированного списка прилагательных (и гарантированно уникальны).
"""

import os
import re
import sys
import json
import time

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

CHUNK_WORDS = 150          # LaBSE обрезает на 256 токенах — режем с запасом
MAX_CHUNKS = 40            # потолок на элемент
RANDOM_STATE = 42

# --- маскируем секреты: в эмбеддинге они бесполезны, а в файлах карты вредны ---
SECRET_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"vless://[^\s\"']+"),
    re.compile(r"(?i)\b(pass(word)?|пароль|local_key|api[_-]?key|token|токен)\b\s*[:=]\s*\S+"),
    re.compile(r"pbkdf2:sha256:[^\s]+|scrypt:[^\s]+"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]


def redact(text):
    for p in SECRET_PATTERNS:
        text = p.sub("<скрыто>", text)
    return text


RU_STOP = set("""
и в во не что он на я с со как а то все всё она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже
или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они
тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем
чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после
над больше тот через эти нас про них какая много разве три эту моя впрочем свою этой перед
иногда лучше чуть том нельзя такой им более всегда конечно всю между это по для из а
файл файлы папка папке проект программа скрипт код версия данные новый свой этих либо
com www http https ru py js html txt md json org github readme the and for with this that
скрыто semin users appdata local roaming programs program files windows desktop onedrive
рабочий стол пользователи common startup start menu микрософт microsoft
src main index build dist test tests temp cache config setup init utils tools assets
import from return class none true false null print self def async await const function
""".split())


def chunk_text(text, n_words=CHUNK_WORDS, max_chunks=MAX_CHUNKS):
    words = text.split()
    if not words:
        return []
    return [" ".join(words[i:i + n_words])
            for i in range(0, len(words), n_words)][:max_chunks]


def build_texts(items, descriptions):
    """Возвращает три словаря текстов: описание / содержимое / признак фолбэка."""
    text_desc, text_cont, fallback = {}, {}, {}

    for it in items:
        name = it["name"]

        # --- A: описание ---
        d = descriptions.get(name, "")
        text_desc[name] = f"{name}. {d}" if d else name

        # --- B: содержимое (без имени элемента — проверяем именно контент) ---
        parts = []
        tree = it.get("tree", [])
        if tree:
            # пути внутри проекта — сами по себе сильный сигнал
            flat = " ".join(p.replace("\\", " ").replace("_", " ").replace("-", " ")
                            for p in tree)
            parts.append(flat)
        exts = it.get("ext_hist", {})
        if exts:
            parts.append(" ".join(f"{k.lstrip('.')} " * min(v, 5) for k, v in exts.items()))
        if it.get("target"):
            parts.append(os.path.basename(it["target"]))
            parts.append(it["target"].replace("\\", " "))
        for sn in it.get("snippets", []):
            parts.append(sn["text"])

        content = redact(" ".join(parts)).strip()
        if len(content) < 60:
            # пустая папка / только бинарники — честного содержимого нет
            content = f"{name} {' '.join(exts.keys())}".strip()
            fallback[name] = True
        else:
            fallback[name] = False
        text_cont[name] = content

    return text_desc, text_cont, fallback


def embed(model, texts_by_name, names, label, cache_path):
    """
    Чанкование -> нормированные эмбеддинги -> среднее -> L2. Возвращает (n, d).

    Кэш по хэшу текста: LaBSE на CPU считает содержимое рабочего стола ~10 минут,
    и пересчитывать неизменившиеся элементы каждый раз незачем.
    """
    import hashlib

    cache = {}
    if os.path.exists(cache_path):
        z = np.load(cache_path, allow_pickle=False)
        keys = z["keys"]
        vals = z["vals"]
        cache = {str(k): vals[i] for i, k in enumerate(keys)}

    def key(n):
        return hashlib.md5(texts_by_name[n].encode("utf-8")).hexdigest()

    todo = [n for n in names if key(n) not in cache]
    print(f"  [{label}] элементов {len(names)}, из кэша {len(names)-len(todo)}, считаем {len(todo)}")

    if todo:
        all_chunks, owner = [], []
        for i, n in enumerate(todo):
            chunks = chunk_text(texts_by_name[n]) or [n]
            all_chunks.extend(chunks)
            owner.extend([i] * len(chunks))

        print(f"  [{label}] {len(all_chunks)} чанков...")
        t0 = time.time()
        vecs = model.encode(all_chunks, convert_to_numpy=True, batch_size=32,
                            normalize_embeddings=True, show_progress_bar=False)
        print(f"  [{label}] за {time.time()-t0:.1f}с")

        owner = np.array(owner)
        for i, n in enumerate(todo):
            v = vecs[owner == i].mean(axis=0)   # среднее УЖЕ нормированных векторов
            nrm = np.linalg.norm(v)
            cache[key(n)] = (v / nrm if nrm else v).astype(np.float32)

        np.savez_compressed(cache_path,
                            keys=np.array(list(cache.keys())),
                            vals=np.stack(list(cache.values())))

    return np.stack([cache[key(n)] for n in names])


def mean_pairwise(X):
    """Средний косинус между всеми парами — мера «насколько всё похоже на всё»."""
    S = X @ X.T
    n = len(X)
    return float((S.sum() - np.trace(S)) / (n * (n - 1)))


def decommon(X):
    """
    Убирает общую компоненту: вычитает средний вектор и снова нормирует.

    У всех папок рабочего стола огромная общая часть — «это питоновский проект
    с requirements.txt». Она одинаково велика у всех и потому не различает,
    зато съедает почти весь косинус: без вычитания 72 из 180 элементов
    сваливаются в один кластер. После вычитания остаётся то, чем элементы
    отличаются друг от друга, — предметная область.
    """
    Y = X - X.mean(axis=0, keepdims=True)
    n = np.linalg.norm(Y, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return Y / n


def pick_k(X, n_items, per_cluster=(7, 15)):
    """
    Число кластеров.

    Silhouette на 768-мерных эмбеддингах почти монотонно убывает по k: у неё
    всегда выигрывает минимум диапазона, сколько бы тем в данных ни было.
    (Ровно из-за этого соц аналитик почти всем аккаунтам ставит k=3.)
    Поэтому диапазон задаётся желаемым размером кластера, а silhouette уже
    внутри него выбирает лучший вариант — она нормально сравнивает соседние k,
    просто не годится как абсолютный критерий.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    hi_size, lo_size = per_cluster
    k_min = max(3, round(n_items / lo_size))
    k_max = max(k_min + 1, round(n_items / hi_size))

    best, best_score, scores = k_min, -2.0, []
    for k in range(k_min, min(k_max, len(X) - 1) + 1):
        km = KMeans(n_clusters=k, random_state=RANDOM_STATE, n_init=10)
        labels = km.fit_predict(X)
        if len(set(labels)) < 2:
            continue
        s = silhouette_score(X, labels, metric="cosine")   # та же метрика, что у UMAP
        scores.append((k, round(float(s), 4)))
        if s > best_score:
            best, best_score = k, s
    print(f"  диапазон k: {k_min}..{k_max} (по {hi_size}-{lo_size} элементов на кластер)")
    print(f"  silhouette: {scores}")
    print(f"  выбрано k={best} (score={best_score:.4f})")
    return best


def name_clusters(labels, names, texts_by_name):
    """Имя кластера = самые характерные для него слова (TF-IDF), имена уникальны."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    docs = []
    for c in sorted(set(labels)):
        members = [names[i] for i in range(len(names)) if labels[i] == c]
        docs.append(" ".join(texts_by_name[m] for m in members))

    vec = TfidfVectorizer(
        max_features=6000, token_pattern=r"(?u)\b[а-яёА-ЯЁa-zA-Z]{4,}\b",
        stop_words=list(RU_STOP), sublinear_tf=True,
    )
    M = vec.fit_transform(docs)
    vocab = np.array(vec.get_feature_names_out())

    used, out = set(), {}
    for row, c in enumerate(sorted(set(labels))):
        order = np.asarray(M[row].todense()).ravel().argsort()[::-1]
        words = []
        for idx in order:
            w = vocab[idx]
            if w in used or len(words) >= 3:
                continue
            words.append(w)
            used.add(w)
            if len(words) == 3:
                break
        out[int(c)] = " · ".join(words) if words else f"кластер {c}"
    return out


def make_variant(label, X, names, texts_by_name):
    import umap
    from sklearn.cluster import KMeans

    print(f"\n=== вариант: {label} ===")
    k = pick_k(X, len(names))
    km = KMeans(n_clusters=k, random_state=RANDOM_STATE, n_init=10)
    labels = km.fit_predict(X)

    def project(n_components):
        r = umap.UMAP(n_neighbors=12, min_dist=0.25, metric="cosine",
                      random_state=RANDOM_STATE, n_components=n_components)
        Z = np.asarray(r.fit_transform(X), dtype=np.float64)
        span = Z.max(axis=0) - Z.min(axis=0)
        span[span == 0] = 1.0
        return (Z - Z.min(axis=0)) / span      # в [0..1] — так удобнее и вьюверу, и значкам

    xy = project(2)
    # отдельная 3D-проекция: это не «2D плюс высота», а честная укладка в три
    # измерения — в трёх осях UMAP есть куда развести то, что в двух слипается
    xyz = project(3)

    cnames = name_clusters(labels, names, texts_by_name)

    # ближайшие соседи в исходном 768-мерном пространстве (не по картинке!)
    sim = X @ X.T
    np.fill_diagonal(sim, -2.0)
    neighbors = {}
    for i, n in enumerate(names):
        top = np.argsort(sim[i])[::-1][:6]
        neighbors[n] = [{"name": names[j], "sim": round(float(sim[i][j]), 3)} for j in top]

    return {
        "k": int(k),
        "cluster_names": {str(c): v for c, v in cnames.items()},
        "points": {names[i]: {"x": float(xy[i, 0]), "y": float(xy[i, 1]),
                              "x3": float(xyz[i, 0]), "y3": float(xyz[i, 1]),
                              "z3": float(xyz[i, 2]),
                              "cluster": int(labels[i])} for i in range(len(names))},
        "neighbors": neighbors,
    }


def main():
    items_blob = json.load(open(os.path.join(DATA, "items.json"), encoding="utf-8"))
    items = items_blob["items"]
    descriptions = json.load(open(os.path.join(DATA, "descriptions.json"),
                                  encoding="utf-8"))["descriptions"]

    names = [it["name"] for it in items]
    missing = [n for n in names if n not in descriptions]
    if missing:
        print(f"ВНИМАНИЕ: нет описаний для {len(missing)}: {missing[:10]}")

    text_desc, text_cont, fallback = build_texts(items, descriptions)
    n_fb = sum(1 for v in fallback.values() if v)
    print(f"Элементов: {len(names)}; без реального содержимого (фолбэк на имя): {n_fb}")

    from sentence_transformers import SentenceTransformer
    print("Загрузка LaBSE...")
    model = SentenceTransformer("sentence-transformers/LaBSE")

    X_desc = embed(model, text_desc, names, "описание",
                   os.path.join(DATA, "cache_desc.npz"))
    X_cont = embed(model, text_cont, names, "содержимое",
                   os.path.join(DATA, "cache_content.npz"))

    print(f"\nСредний косинус внутри 'описание': {mean_pairwise(X_desc):.3f}, "
          f"внутри 'содержимое': {mean_pairwise(X_cont):.3f} "
          f"(чем ближе к 1, тем сильнее общая компонента давит различия)")

    X_desc = decommon(X_desc)
    X_cont = decommon(X_cont)
    print(f"После вычитания общей компоненты: {mean_pairwise(X_desc):.3f} / "
          f"{mean_pairwise(X_cont):.3f}")

    X_hyb = X_desc + X_cont
    X_hyb /= np.linalg.norm(X_hyb, axis=1, keepdims=True)

    variants = {
        "описание": make_variant("описание", X_desc, names, text_desc),
        "содержимое": make_variant("содержимое", X_cont, names, text_cont),
        "гибрид": make_variant("гибрид", X_hyb, names, text_desc),
    }

    # насколько два подхода вообще согласны друг с другом
    agree = float(np.mean(np.sum(X_desc * X_cont, axis=1)))
    print(f"\nСредняя косинусная близость 'описание' vs 'содержимое': {agree:.3f}")

    meta = {it["name"]: {
        "kind": it["kind"], "is_dir": it["is_dir"], "path": it["path"],
        "size_human": it.get("size_human", ""), "n_files": it.get("n_files", 0),
        "mtime": it["mtime"], "ext_hist": it.get("ext_hist", {}),
        "target": it.get("target", ""),
        "description": descriptions.get(it["name"], ""),
        "content_fallback": fallback.get(it["name"], False),
    } for it in items}

    out = {
        "desktop": items_blob["desktop"],
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_items": len(names),
        "desc_vs_content_cosine": round(agree, 4),
        "meta": meta,
        "variants": variants,
    }
    path = os.path.join(DATA, "map.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    np.save(os.path.join(DATA, "vectors_desc.npy"), X_desc)
    np.save(os.path.join(DATA, "vectors_content.npy"), X_cont)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
