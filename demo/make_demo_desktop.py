# -*- coding: utf-8 -*-
"""
Генератор демонстрационного «рабочего стола».

Нужен, чтобы показывать карту на скриншотах, не показывая чужие настоящие
папки. Создаёт дерево выдуманных проектов вымышленного человека: немного
электроники, немного вебa, игры, учёба, фотография, музыка, плюс ярлыки
программ и пара пустых папок-заготовок — чтобы на карте было что кластеризовать.

    python demo/make_demo_desktop.py            # создать demo/desktop
    python scan_desktop.py demo/desktop         # просканировать его вместо своего
    python build_map.py
    python serve.py

Все имена, тексты и код внутри — выдуманные.
"""

import os
import sys
import json
import shutil

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "desktop")

PY = "# -*- coding: utf-8 -*-\n"

# --- проекты: имя -> {файл: содержимое} ---------------------------------------
PROJECTS = {
    # электроника и физический мир
    "метеостанция на ардуино": {
        "README.md": "# Домашняя метеостанция\n\nArduino Nano, датчик BME280, дисплей на SSD1306.\n"
                     "Каждые 30 секунд снимает температуру, влажность и давление,\n"
                     "пишет в SD-карту и рисует график за сутки.\n",
        "station.ino": "#include <Wire.h>\n#include <Adafruit_BME280.h>\n\n"
                       "Adafruit_BME280 bme;\nvoid setup() { Serial.begin(9600); bme.begin(0x76); }\n"
                       "void loop() { Serial.println(bme.readTemperature()); delay(30000); }\n",
        "график.py": PY + "import pandas as pd, matplotlib.pyplot as plt\n"
                          "df = pd.read_csv('log.csv')\ndf.plot(x='time', y='temp')\nplt.show()\n",
        "правки.txt": "вынести датчик за окно, а то греется от платы\nдобавить давление на график\n",
    },
    "умная теплица": {
        "README.md": "# Теплица\n\nПолив по влажности почвы, форточка на сервоприводе,\n"
                     "лампа досветки по расписанию. Управление с телефона через ESP32.\n",
        "greenhouse.ino": "#define PUMP 5\n#define SOIL A0\nvoid loop() {\n"
                          "  if (analogRead(SOIL) > 700) digitalWrite(PUMP, HIGH);\n}\n",
        "калибровка почвы.txt": "сухая земля 850\nполитая 320\nпорог ставим 700\n",
    },
    "робот пылесос разбор": {
        "заметки.md": "# Что внутри пылесоса\n\nРазобрал, нашёл UART на плате.\n"
                      "Лидар крутится отдельным мотором, данные идут пакетами по 22 байта.\n",
        "lidar_read.py": PY + "import serial\ns = serial.Serial('COM4', 115200)\n"
                              "while True:\n    pkt = s.read(22)\n    print(pkt.hex())\n",
    },
    "плата для гитары": {
        "описание.txt": "Педаль перегруза на LM386. Развожу плату в KiCad.\n"
                        "Хочу три ручки: gain, tone, volume.\n",
        "bom.csv": "позиция,номинал,корпус\nR1,10k,0805\nC3,100n,0805\nU1,LM386,DIP-8\n",
    },

    # веб и сервисы
    "сайт мастерской": {
        "README.md": "# Сайт столярной мастерской\n\nDjango, галерея работ, форма заказа,\n"
                     "админка для загрузки фотографий изделий.\n",
        "manage.py": PY + "import django\nfrom django.core.management import execute_from_command_line\n",
        "requirements.txt": "Django==5.0\npillow\ngunicorn\n",
        "models.py": PY + "from django.db import models\n\n"
                          "class Work(models.Model):\n    title = models.CharField(max_length=200)\n"
                          "    photo = models.ImageField(upload_to='works/')\n",
    },
    "телеграм бот для заметок": {
        "README.md": "# Бот-блокнот\n\nПрисылаешь боту текст — он складывает в базу и умеет искать.\n"
                     "Теги через решётку, напоминания через /remind.\n",
        "bot.py": PY + "from telegram.ext import Application, MessageHandler\n\n"
                       "async def save(update, ctx):\n    db.insert(update.message.text)\n",
        "requirements.txt": "python-telegram-bot==21.0\nsqlalchemy\n",
    },
    "трекер расходов": {
        "README.md": "# Куда уходят деньги\n\nПарсит выписку из банка, раскладывает траты\n"
                     "по категориям и рисует, сколько ушло на еду, транспорт и всякую ерунду.\n",
        "parse.py": PY + "import pandas as pd\ndf = pd.read_csv('statement.csv')\n"
                         "df['cat'] = df['description'].map(classify)\n",
        "категории.json": json.dumps({"еда": ["пятёрочка", "магнит"],
                                      "транспорт": ["метро", "такси"]},
                                     ensure_ascii=False, indent=1),
    },
    "погодный виджет": {
        "index.html": "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
                      "<title>Погода</title></head>\n<body><div id='now'></div></body></html>\n",
        "app.js": "async function load() {\n  const r = await fetch(API + city);\n"
                  "  document.getElementById('now').textContent = (await r.json()).temp;\n}\n",
    },

    # данные и обучение
    "распознавание рукописных цифр": {
        "README.md": "# Цифры\n\nСвёрточная сеть на MNIST, потом дообучение на своём почерке.\n"
                     "Точность на тесте 99.1%.\n",
        "train.py": PY + "import torch, torch.nn as nn\n\n"
                         "model = nn.Sequential(nn.Conv2d(1,32,3), nn.ReLU(), nn.Flatten())\n",
        "правки.txt": "попробовать аугментацию поворотом\nсравнить с обычным перцептроном\n",
    },
    "предсказание урожая": {
        "README.md": "# Урожай\n\nПо погоде за сезон и типу почвы предсказываем урожайность.\n"
                     "Градиентный бустинг, данные за восемь лет.\n",
        "model.py": PY + "from sklearn.ensemble import GradientBoostingRegressor\n"
                         "m = GradientBoostingRegressor().fit(X, y)\n",
        "данные.csv": "год,осадки,средняя_температура,урожай\n2019,410,17.2,32\n2020,380,18.1,29\n",
    },
    "анализ школьных оценок": {
        "заметки.md": "# Оценки\n\nВыгрузил журнал, посмотрел корреляцию между посещаемостью\n"
                      "и итоговой оценкой. Связь есть, но слабее, чем ожидал.\n",
        "analyze.py": PY + "import pandas as pd\nprint(df.corr()['итог'])\n",
    },
    "кластеризация музыки": {
        "README.md": "# Похожая музыка\n\nБерём спектрограммы треков, считаем эмбеддинги,\n"
                     "раскладываем в 2D и смотрим, какие жанры оказались рядом.\n",
        "embed.py": PY + "import librosa, numpy as np\n"
                         "y, sr = librosa.load(path)\nmel = librosa.feature.melspectrogram(y=y, sr=sr)\n",
    },

    # игры
    "змейка на паскале": {
        "snake.pas": "program Snake;\nvar x, y: integer;\nbegin\n  writeln('игра');\nend.\n",
        "почему паскаль.txt": "нашёл старый учебник, захотелось вспомнить\n",
    },
    "платформер на годо": {
        "README.md": "# Платформер\n\nGodot 4, пиксель-арт, три уровня.\n"
                     "Двойной прыжок, движущиеся платформы, шипы.\n",
        "player.gd": "extends CharacterBody2D\n\nfunc _physics_process(delta):\n"
                     "    velocity.y += gravity * delta\n    move_and_slide()\n",
        "идеи уровней.txt": "уровень с водой\nуровень где гравитация переворачивается\n",
    },
    "генератор подземелий": {
        "README.md": "# Подземелья\n\nКомнаты расставляются случайно, потом соединяются\n"
                     "коридорами по минимальному остовному дереву. Немного циклов добавляем обратно.\n",
        "dungeon.py": PY + "import random\n\ndef rooms(n):\n"
                           "    return [(random.randint(0,80), random.randint(0,40)) for _ in range(n)]\n",
    },
    "мод на майнкрафт": {
        "README.md": "# Мод\n\nДобавляет руду, из которой плавится новый металл,\n"
                     "и инструменты из него. Fabric, версия 1.20.\n",
        "fabric.mod.json": json.dumps({"id": "coppertools", "version": "0.2.0"}, indent=1),
    },

    # фото и видео
    "склейка панорам": {
        "README.md": "# Панорамы\n\nБерём серию кадров, ищем общие точки, склеиваем.\n"
                     "OpenCV, ручная правка швов.\n",
        "stitch.py": PY + "import cv2\nst = cv2.Stitcher_create()\nstatus, pano = st.stitch(images)\n",
    },
    "таймлапс облаков": {
        "заметки.txt": "снимаю с балкона раз в 10 секунд\nлучше всего выходит перед грозой\n"
                       "нужен ND-фильтр, днём пересвет\n",
        "collect.py": PY + "import time\nwhile True:\n    camera.capture()\n    time.sleep(10)\n",
    },
    "сортировка фотоархива": {
        "README.md": "# Разбор архива\n\nРаскладывает фотографии по годам и месяцам из EXIF,\n"
                     "находит дубликаты по перцептивному хешу.\n",
        "sort.py": PY + "from PIL import Image\nimport imagehash\nh = imagehash.phash(Image.open(p))\n",
    },

    # музыка
    "синтезатор на питоне": {
        "README.md": "# Синтезатор\n\nADSR-огибающая, три формы волны, простой фильтр.\n"
                     "Играть можно с клавиатуры компьютера.\n",
        "synth.py": PY + "import numpy as np, sounddevice as sd\n"
                         "def saw(f, t): return 2*(t*f - np.floor(0.5 + t*f))\n",
    },
    "разбор гитарных табов": {
        "заметки.md": "# Табы\n\nПарсер текстовых табулатур в MIDI, чтобы слышать,\n"
                      "правильно ли я прочитал ритм.\n",
        "tab2midi.py": PY + "import mido\ntrack = mido.MidiTrack()\n",
    },

    # учёба и документы
    "конспекты по физике": {
        "механика.md": "# Механика\n\nЗаконы Ньютона, импульс, энергия.\n"
                       "Задачи на наклонную плоскость с трением.\n",
        "оптика.md": "# Оптика\n\nПреломление, линзы, формула тонкой линзы.\n",
    },
    "подготовка к экзамену": {
        "план.txt": "понедельник — интегралы\nвторник — ряды\nсреда — дифуры\nчетверг — повторение\n",
        "формулы.md": "# Шпаргалка\n\nПроизводные, интегралы по частям, признаки сходимости.\n",
    },
    "курсовая по теплотехнике": {
        "текст.md": "# Расчёт теплообменника\n\nВходные данные, тепловой баланс,\n"
                    "подбор поверхности теплообмена, выводы.\n",
        "расчёт.py": PY + "Q = m * c * (t2 - t1)\nprint(f'тепловая мощность {Q:.1f} Вт')\n",
    },

    # утилиты
    "переименователь файлов": {
        "rename.py": PY + "import os, re\nfor f in os.listdir('.'):\n"
                          "    os.rename(f, re.sub(r'\\s+', '_', f.lower()))\n",
    },
    "бэкап на флешку": {
        "backup.ps1": "$src = 'C:\\work'\n$dst = 'E:\\backup'\n"
                      "robocopy $src $dst /MIR /R:1\n",
        "что копировать.txt": "документы\nпроекты\nключи\nфотографии за этот год\n",
    },
    "чистка диска": {
        "заметки.txt": "самое жирное — кэши сборок и старые образы докера\n"
                       "docker system prune освободил 40 гигабайт\n",
    },
}

# короткие файлы прямо на столе
LOOSE_FILES = {
    "список покупок.txt": "паяльник\nтермоусадка\nмакетная плата\nпровода перемычки\n",
    "пароль от роутера.txt": "лежит на самом роутере, снизу на наклейке\n",
    "команды git.txt": "git switch -c ветка\ngit rebase -i HEAD~3\ngit stash pop\n",
    "идеи проектов.txt": "часы на нике-лампах\nкормушка для птиц с камерой\n"
                         "карта шума в районе\nсвой поисковик по конспектам\n",
    "калькулятор.html": "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        "<title>Калькулятор</title></head><body><input id='a'></body></html>\n",
    "часы.html": "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Часы</title></head>"
                 "<body><canvas id='c'></canvas><script>setInterval(draw,1000)</script></body></html>\n",
}

# пустые папки — на карте они собираются в отдельный кластер
EMPTY = ["сделать сайт портфолио", "починить наушники", "3д принтер купить",
         "выучить японский", "разобрать гараж"]

# ярлыки: имитируем .lnk текстовым файлом с расширением .url — сканер увидит их
# как файлы, но по имени и содержимому они всё равно соберутся в свой кластер
SHORTCUTS = {
    "Blender": "3D-моделирование и рендеринг",
    "KiCad": "разводка печатных плат",
    "Godot": "движок для игр",
    "VS Code": "редактор кода",
    "GIMP": "растровый редактор изображений",
    "Audacity": "редактор звука",
    "OBS": "запись экрана и трансляции",
    "Krita": "рисование и цифровая живопись",
    "Inkscape": "векторная графика",
    "FreeCAD": "параметрическое 3D-проектирование",
    "qBittorrent": "торрент-клиент",
    "VLC": "видеоплеер",
}


def main():
    if os.path.exists(ROOT):
        shutil.rmtree(ROOT)
    os.makedirs(ROOT)

    for name, files in PROJECTS.items():
        d = os.path.join(ROOT, name)
        os.makedirs(d, exist_ok=True)
        for fn, text in files.items():
            with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                f.write(text)

    for name in EMPTY:
        os.makedirs(os.path.join(ROOT, name), exist_ok=True)

    for fn, text in LOOSE_FILES.items():
        with open(os.path.join(ROOT, fn), "w", encoding="utf-8") as f:
            f.write(text)

    for name, what in SHORTCUTS.items():
        with open(os.path.join(ROOT, f"{name}.url"), "w", encoding="utf-8") as f:
            f.write(f"[InternetShortcut]\nURL=file:///C:/Programs/{name}/{name}.exe\n"
                    f"; {what}\n")

    n = len(os.listdir(ROOT))
    print(f"Демо-стол собран: {n} элементов")
    print(f"  {len(PROJECTS)} проектов, {len(EMPTY)} пустых заготовок, "
          f"{len(LOOSE_FILES)} файлов, {len(SHORTCUTS)} ярлыков")
    print(f"-> {ROOT}")
    print("\nДальше:")
    print("  python scan_desktop.py demo/desktop")
    print("  python build_map.py")
    print("  python serve.py")


if __name__ == "__main__":
    main()
