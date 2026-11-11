import sqlite3
import time

import requests
from bs4 import BeautifulSoup

OWNER = "google"
URL = f"https://github.com/{OWNER}?tab=repositories"
DB_NAME = "repositories.db"


def scrape_repositories():
    # ページにアクセスする前に1秒待つ
    time.sleep(1)

    response = requests.get(
        URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    repositories = []

    # GitHubのリポジトリ一覧にあるリポジトリ名のリンクを探す
    repo_links = soup.select('a[itemprop="name codeRepository"]')

    for repo_link in repo_links:
        href = repo_link.get("href", "")
        name = href.strip("/").split("/")[-1]

        if not name:
            continue

        # リポジトリ情報を含む親要素から言語とスター数を探す
        container = repo_link.find_parent("li")
        if container is None:
            container = repo_link.parent

        language_tag = container.select_one(
            '[itemprop="programmingLanguage"]'
        )
        language = (
            language_tag.get_text(strip=True)
            if language_tag
            else "不明"
        )

        star_link = container.select_one('a[href$="/stargazers"]')
        stars = star_link.get_text(strip=True) if star_link else "不明"

        repositories.append((name, language, stars))

    if not repositories:
        raise RuntimeError(
            "リポジトリを取得できませんでした。"
            "GitHubのページ構造が変わった可能性があります。"
        )

    return repositories


def main():
    repositories = scrape_repositories()
    print(f"{len(repositories)}件のリポジトリを取得しました。")

    with sqlite3.connect(DB_NAME) as connection:
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

        # DBに保存したデータをSELECTして表示する
        cursor.execute("""
            SELECT name, language, stars
            FROM repositories
            ORDER BY name
        """)

        print("\nデータベースの内容:")
        for name, language, stars in cursor.fetchall():
            print(f"{name} | {language} | ★ {stars}")


if __name__ == "__main__":
    main()
