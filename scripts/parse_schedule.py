"""Разовый скрипт: Excel с расписанием всего института -> JSON по одной группе."""
import json, re, sys
import pandas as pd

SRC = "/mnt/user-data/uploads/raspisanie_o_iita_1_26_27.xlsx"
GROUP = "4-МД-4"
DAYS = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота","Воскресенье"]
REMOTE_MARKERS = ("Дистанционное обучение",)

def clean(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).replace("_x000D_", " ")
    return re.sub(r"\s+", " ", s).strip()

df = pd.read_excel(SRC, sheet_name="Sheet")
g = df[df["Группа"] == GROUP]
if g.empty:
    sys.exit(f"Группа {GROUP} не найдена")

lessons = []
for i, r in g.iterrows():
    building = clean(r["Корпус"])
    parity = clean(r["Счет недель"])          # числ | знам | числ/знам
    start, end = clean(r["Время"]).split("-")
    lessons.append({
        "id": f"L{i}",
        "weekday": DAYS.index(clean(r["День недели"])) + 1,   # 1=Пн
        "start": start, "end": end,
        "parity": {"числ": "odd", "знам": "even", "числ/знам": "both"}[parity],
        "subject": clean(r["Дисциплина"]),
        "kind": clean(r["Вид занятий"]),
        "teacher": clean(r["Преподаватель"]),
        "building": building,
        "room": clean(r["Аудитория"]),
        "is_remote": building in REMOTE_MARKERS,
    })
lessons.sort(key=lambda x: (x["weekday"], x["start"]))

out = {
    "group": GROUP,
    "course_semester": clean(g["Курс/Семестр"].iloc[0]),
    "buildings": sorted({l["building"] for l in lessons if not l["is_remote"]}),
    "lessons": lessons,
}
with open("data/schedule_4md4.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"занятий: {len(lessons)} | корпусов: {len(out['buildings'])} | дистант: {sum(l['is_remote'] for l in lessons)}")
for wd in range(1, 8):
    day = [l for l in lessons if l["weekday"] == wd]
    if not day: continue
    odd  = [l for l in day if l["parity"] in ("odd","both")]
    even = [l for l in day if l["parity"] in ("even","both")]
    f = lambda xs: (xs[0]["start"] + " " + ("ДО" if xs[0]["is_remote"] else xs[0]["building"][:14])) if xs else "— пар нет"
    print(f"{DAYS[wd-1]:12} числ: {f(odd):22} знам: {f(even)}")
