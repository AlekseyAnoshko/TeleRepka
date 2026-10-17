"""Синхронизация сотрудников лаборатории с официальным сайтом ИРНИТУ."""

import argparse
import re
import sqlite3
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from employees import connect, init_db

SOURCE = "https://www.istu.edu/struktura/setevykh_tekhnologiy/sotrudniki"
PERSON_RE = re.compile(r"^/person/(\d+)/?$")


def normalize_name(value):
    return " ".join(unicodedata.normalize("NFC", value).split())


def name_key(value):
    parts = normalize_name(value).split()
    if len(parts) < 2:
        return None

    # Фамилия + инициалы. Сопоставляет полное ФИО с «Аношко А. Ф.».
    initials = "".join(part[0] for part in parts[1:])
    return (parts[0].casefold(), initials.casefold())


class PersonParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.text = []
        self.people = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if PERSON_RE.fullmatch(href):
                self.href = href
                self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            name = normalize_name(" ".join(self.text))
            if len(name.split()) >= 3:
                self.people.append({
                    "name": name,
                    "profile_url": urljoin(SOURCE, self.href),
                })
            self.href = None
            self.text = []


def fetch_people():
    response = requests.get(
        SOURCE,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    if urlparse(response.url).hostname != "www.istu.edu":
        raise RuntimeError("Неожиданный адрес ответа")

    parser = PersonParser()
    parser.feed(response.text)

    by_url = {}
    for person in parser.people:
        by_url[person["profile_url"]] = person

    people = list(by_url.values())

    # Защита от ошибочного HTML и неполной загрузки.
    if not 12 <= len(people) <= 100:
        raise RuntimeError(
            f"Неожиданное количество сотрудников: {len(people)}"
        )

    if len({name_key(p["name"]) for p in people}) != len(people):
        raise RuntimeError("Обнаружены повторяющиеся ФИО")

    return people


def synchronize(people, apply=False):
    with connect() as conn:
        existing = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, profile_url, istu_id FROM employees"
            )
        ]

        by_profile = {
            p["profile_url"]: p
            for p in existing if p["profile_url"]
        }
        by_name = {
            name_key(p["name"]): p
            for p in existing if name_key(p["name"])
        }

        added = 0
        updated = 0

        if apply:
            conn.execute("BEGIN IMMEDIATE")

        for person in people:
            old = (
                by_profile.get(person["profile_url"])
                or by_name.get(name_key(person["name"]))
            )

            if old:
                updated += 1
                print(
                    f"ОБНОВИТЬ: {person['name']} "
                    f"(id={old['id']}, расписание={old['istu_id']})"
                )
                if apply:
                    conn.execute("""
                        UPDATE employees
                        SET name = ?, profile_url = ?,
                            active = 1,
                            last_seen_at = datetime('now')
                        WHERE id = ?
                    """, (
                        person["name"],
                        person["profile_url"],
                        old["id"],
                    ))
            else:
                added += 1
                print("ДОБАВИТЬ:", person["name"])
                if apply:
                    conn.execute("""
                        INSERT INTO employees
                            (name, profile_url, active, last_seen_at)
                        VALUES (?, ?, 1, datetime('now'))
                    """, (person["name"], person["profile_url"]))

        if apply:
            conn.commit()

    print(f"\nДобавить: {added}; обновить: {updated}")
    print("Изменения сохранены." if apply else "Предпросмотр: база не изменена.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения к SQLite",
    )
    args = parser.parse_args()

    init_db()
    people = fetch_people()
    print(f"Сотрудников на сайте: {len(people)}\n")
    synchronize(people, apply=args.apply)


if __name__ == "__main__":
    main()
