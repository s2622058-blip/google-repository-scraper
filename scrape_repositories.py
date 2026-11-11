import sqlite3
import time

import requests
from bs4 import BeautifulSoup

OWNER = "google"
URL = f"https://github.com/{OWNER}?tab=repositories"
DB_NAME = "repositories.db"


def scrape_repositories():
    response = requests.get(
        URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    repositories = []

    for card in soup.select("article"):
        name_link = card.select_one("h3 a")
        if not name_link:
            continue

        name = name_link.get_text(" ", strip=True).replace(" ", "")
        language_tag = card.select_one('[itemprop="programmingLanguage"]')
        language = language_tag.get_text(strip=True) if language_tag else "不明"

        star_link = card.select_one('a[href$="/stargazers"]')
        stars = star_link.get_text(strip=True) if star_link else "0"

        repositories.append((name, language, stars))

    return repositories


def main():
    repositories = scrape_repositories()

    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS repositories (
            name TEXT PRIMARY KEY,
            language TEXT,
            stars TEXT
        )
    """)

    cursor.executemany(
        """
        INSERT OR REPLACE INTO repositories (name, language, stars)
        VALUES (?, ?, ?)
        """,
        repositories,
    )
    connection.commit()

    # アクセスの間に1秒待つ
    time.sleep(1)

    cursor.execute("SELECT name, language, stars FROM repositories")
    for name, language, stars in cursor.fetchall():
        print(f"{name} | {language} | ★ {stars}")

    connection.close()


if __name__ == "__main__":
    main()
