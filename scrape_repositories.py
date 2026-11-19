import sqlite3
import time

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://github.com/orgs/google/repositories?type=all"
DB_NAME = "repositories.db"
MAX_PAGES = 150
MAX_RETRIES = 8


def prepare_database():
    """データ保存用テーブルと再開位置を準備する。"""
    with sqlite3.connect(DB_NAME) as connection:
        cursor = connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS repositories (
                name TEXT PRIMARY KEY,
                language TEXT,
                stars TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scrape_progress (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_page INTEGER NOT NULL,
                completed INTEGER NOT NULL
            )
        """)

        cursor.execute("""
            SELECT last_page, completed
            FROM scrape_progress
            WHERE id = 1
        """)
        progress = cursor.fetchone()

        if progress is None or progress[1] == 1:
            # 初回または前回の取得が完了済みなら、最初から取り直す
            cursor.execute("DELETE FROM repositories")
            cursor.execute("""
                INSERT OR REPLACE INTO scrape_progress (id, last_page, completed)
                VALUES (1, 0, 0)
            """)
            return 1

        # 中断していた場合は、最後に保存した次のページから再開
        return progress[0] + 1


def get_repository_links(soup):
    """ページ内のGoogleリポジトリ名リンクを探す。"""
    links = soup.select('h3 a[href^="/google/"]')

    if not links:
        links = soup.select('a[itemprop="name codeRepository"]')

    return links


def find_repository_card(repo_link, name):
    """言語名とスター数を含むリポジトリカードを探す。"""
    expected_star_href = f"/google/{name}/stargazers"
    fallback_card = None

    for parent in repo_link.parents:
        star_link = parent.select_one(f'a[href="{expected_star_href}"]')

        if star_link and fallback_card is None:
            fallback_card = parent

        language_tag = parent.select_one(
            'span[class*="PrimaryLanguageName"]'
        )

        if star_link and language_tag:
            return parent

    return fallback_card or repo_link.parent


def get_language(card):
    """リポジトリカードから主要言語を取得する。"""
    language_tag = card.select_one(
        'span[class*="PrimaryLanguageName"]'
    )

    if language_tag:
        return language_tag.get_text(strip=True)

    return "不明"


def get_stars(card, name):
    """リポジトリカードからスター数を取得する。"""
    expected_star_href = f"/google/{name}/stargazers"
    star_link = card.select_one(f'a[href="{expected_star_href}"]')

    if not star_link:
        return "不明"

    return star_link.get_text(" ", strip=True)


def request_page(session, page_url):
    """ページを取得し、429の場合は待ってから再試行する。"""
    for attempt in range(MAX_RETRIES):
        # ページにアクセスする前に1秒待つ
        time.sleep(1)

        response = session.get(page_url, timeout=30)

        rate_limited = response.status_code == 429
        if response.status_code == 403:
            response_text = response.text.lower()
            rate_limited = (
                "rate limit" in response_text
                or response.headers.get("Retry-After") is not None
            )

        if not rate_limited:
            response.raise_for_status()
            return response

        if attempt == MAX_RETRIES - 1:
            response.raise_for_status()

        retry_after = response.headers.get("Retry-After")
        reset_time = response.headers.get("X-RateLimit-Reset")

        if retry_after:
            wait_seconds = int(retry_after)
        elif reset_time:
            wait_seconds = max(60, int(reset_time) - int(time.time()))
        else:
            # 待ち時間を60秒、120秒、240秒……と延ばす
            wait_seconds = min(900, 60 * (2 ** attempt))

        print(
            f"GitHubからアクセス制限が返されました。"
            f"{wait_seconds}秒待って同じページを再試行します。"
        )
        time.sleep(wait_seconds)

    raise RuntimeError("ページを取得できませんでした。")


def save_page(page_number, page_repositories):
    """ページの取得結果と進捗をDBに保存する。"""
    with sqlite3.connect(DB_NAME) as connection:
        cursor = connection.cursor()

        cursor.executemany(
            """
            INSERT OR REPLACE INTO repositories (name, language, stars)
            VALUES (?, ?, ?)
            """,
            page_repositories,
        )

        cursor.execute("""
            UPDATE scrape_progress
            SET last_page = ?, completed = 0
            WHERE id = 1
        """, (page_number,))


def mark_complete():
    """全ページの取得完了をDBに記録する。"""
    with sqlite3.connect(DB_NAME) as connection:
        connection.execute("""
            UPDATE scrape_progress
            SET completed = 1
            WHERE id = 1
        """)


def scrape_repositories():
    start_page = prepare_database()

    # 中断後に再開するとき、DBに保存済みのデータも読み込む
    with sqlite3.connect(DB_NAME) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT name, language, stars FROM repositories")
        repositories = {
            row[0]: (row[0], row[1], row[2])
            for row in cursor.fetchall()
        }

    print(f"{start_page}ページ目から取得します。")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    previous_page_names = None

    for page_number in range(start_page, MAX_PAGES + 1):
        page_url = f"{BASE_URL}&page={page_number}"
        response = request_page(session, page_url)
        soup = BeautifulSoup(response.text, "html.parser")
        repo_links = get_repository_links(soup)

        if not repo_links:
            if page_number == 1:
                title = soup.title.get_text(strip=True) if soup.title else "不明"
                raise RuntimeError(
                    "1ページ目からリポジトリを取得できませんでした。"
                    f"ページタイトル: {title}"
                )

            print(f"{page_number}ページ目にデータがないため終了します。")
            mark_complete()
            break

        current_page_names = set()
        page_repositories = []

        for repo_link in repo_links:
            href = repo_link.get("href", "").strip("/")
            parts = href.split("/")

            # /google/リポジトリ名 の形式だけを使う
            if len(parts) != 2 or parts[0] != "google":
                continue

            name = parts[1]
            current_page_names.add(name)

            card = find_repository_card(repo_link, name)
            language = get_language(card)
            stars = get_stars(card, name)

            row = (name, language, stars)
            page_repositories.append(row)
            repositories[name] = row

        if not current_page_names:
            raise RuntimeError(
                f"{page_number}ページ目からリポジトリ名を取得できませんでした。"
            )

        if current_page_names == previous_page_names:
            raise RuntimeError(
                f"{page_number}ページ目が前のページと同じ内容です。"
                "ページ送りができていないため、取得を止めました。"
            )

        # 各ページごとに保存するので、中断しても続きから再開できる
        save_page(page_number, page_repositories)
        previous_page_names = current_page_names

        unknown_count = sum(
            1 for _, language, _ in page_repositories
            if language == "不明"
        )
        print(
            f"{page_number}ページ目: {len(page_repositories)}件"
            f"（累計 {len(repositories)}件、言語不明 {unknown_count}件）"
        )
    else:
        raise RuntimeError(
            f"{MAX_PAGES}ページまで取得しました。"
            "全件取得できたか確認してください。"
        )

    return list(repositories.values())


def main():
    repositories = scrape_repositories()
    print(f"\n合計 {len(repositories)}件を取得しました。")

    with sqlite3.connect(DB_NAME) as connection:
        cursor = connection.cursor()

        # 保存したデータをSELECTして表示する
        cursor.execute("""
            SELECT name, language, stars
            FROM repositories
            ORDER BY name
        """)
        rows = cursor.fetchall()

        unknown_count = sum(
            1 for _, language, _ in rows
            if language == "不明"
        )

        print(f"\nSELECT結果: {len(rows)}件")
        print(f"主要言語が不明のリポジトリ: {unknown_count}件")

        for name, language, stars in rows:
            print(f"{name} | {language} | ★ {stars}")


if __name__ == "__main__":
    main()