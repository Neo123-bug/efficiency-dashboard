"""
Browser data fetcher service (Playwright version)
"""
import re
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sync_playwright = None
    PWTimeout = Exception


def cas_login(page, username, password, target_url=None):
    if not username or not password:
        print('[CAS] empty credentials, skip')
        return False
    try:
        if target_url:
            page.goto(target_url, wait_until='networkidle', timeout=30000)
        else:
            page.goto('https://cas.aihuishou.com/login', wait_until='networkidle', timeout=30000)
        if 'login' not in page.url.lower():
            print('[CAS] already logged in')
            return True
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state('networkidle', timeout=15000)
        if 'login' in page.url.lower():
            print('[CAS] login failed')
            return False
        print('[CAS] login success')
        return True
    except PWTimeout:
        print('[CAS] login timeout')
        return False
    except Exception as e:
        print(f'[CAS] login error: {e}')
        return False


def fetch_godzilla_hour(page, date_str, hour):
    if hour == 12:
        return []
    results = []
    try:
        print(f'[Godzilla] fetch {date_str} {hour}:00 - TODO')
    except Exception as e:
        print(f'[Godzilla] fetch error {date_str} {hour}: {e}')
    return results


def fetch_mirror_hour(page, date_str, hour):
    if hour == 12:
        return []
    results = []
    try:
        print(f'[Mirror] fetch {date_str} {hour}:00 - TODO')
    except Exception as e:
        print(f'[Mirror] fetch error {date_str} {hour}: {e}')
    return results


def calc_work_hours(first_record_minute, hour):
    if hour == 12:
        return 0.0
    if first_record_minute is None:
        return 0.0
    if first_record_minute >= 30:
        return 0.5
    else:
        return 1.0


def save_records(db, date_str, hour, records, data_source):
    count = 0
    for record in records:
        staff_no = record.get('staff_no', '')
        finish_count = int(record.get('finish_count', 0))
        first_min = record.get('first_record_minute', None)
        if not staff_no or finish_count <= 0:
            continue
        work_hours = calc_work_hours(first_min, hour)
        if work_hours <= 0:
            continue
        efficiency = round(finish_count / work_hours, 1) if work_hours > 0 else 0
        try:
            db.execute(
                'DELETE FROM efficiency_record WHERE record_date=? AND hour=? AND staff_no=?',
                (date_str, hour, staff_no)
            )
            db.execute(
                'INSERT INTO efficiency_record(record_date, hour, staff_no, finish_count, '
                'work_hours, efficiency, data_source) VALUES(?,?,?,?,?,?,?)',
                (date_str, hour, staff_no, finish_count, work_hours, efficiency, data_source)
            )
            count += 1
        except Exception as e:
            print(f'  Save failed {staff_no}: {e}')
    if count > 0:
        db.commit()
    return count


def load_config_from_db(db):
    config = {}
    keys = ['cas_username', 'cas_password', 'godzilla_base_url', 'mirror_base_url']
    for key in keys:
        row = db.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
        config[key] = row['value'] if row else ''
    return config


def fetch_and_save(db, date_str, hour, config=None):
    if hour == 12:
        return {'godzilla': 0, 'mirror': 0, 'total': 0}
    if config is None:
        config = load_config_from_db(db)
    username = config.get('cas_username', '')
    password = config.get('cas_password', '')
    godzilla_url = config.get('godzilla_base_url', '')
    mirror_url = config.get('mirror_base_url', '')
    if sync_playwright is None:
        print('[BrowserFetcher] Playwright not installed')
        return {'godzilla': 0, 'mirror': 0, 'total': 0, 'error': 'playwright_not_installed'}
    if not username or not password:
        print('[BrowserFetcher] CAS not configured')
        return {'godzilla': 0, 'mirror': 0, 'total': 0, 'error': 'cas_not_configured'}
    total_count = 0
    godzilla_count = 0
    mirror_count = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = context.new_page()
            if godzilla_url:
                try:
                    print(f'[BrowserFetcher] Fetching Godzilla {date_str} {hour}:00 ...')
                    login_ok = cas_login(page, username, password, target_url=godzilla_url)
                    if login_ok:
                        godzilla_records = fetch_godzilla_hour(page, date_str, hour)
                        godzilla_count = save_records(db, date_str, hour, godzilla_records, 'godzilla')
                        print(f'[BrowserFetcher] Godzilla saved {godzilla_count} records')
                except Exception as e:
                    print(f'[BrowserFetcher] Godzilla error: {e}')
            if mirror_url:
                try:
                    print(f'[BrowserFetcher] Fetching Mirror {date_str} {hour}:00 ...')
                    login_ok = cas_login(page, username, password, target_url=mirror_url)
                    if login_ok:
                        mirror_records = fetch_mirror_hour(page, date_str, hour)
                        mirror_count = save_records(db, date_str, hour, mirror_records, 'mirror')
                        print(f'[BrowserFetcher] Mirror saved {mirror_count} records')
                except Exception as e:
                    print(f'[BrowserFetcher] Mirror error: {e}')
            browser.close()
    except Exception as e:
        print(f'[BrowserFetcher] Browser start error: {e}')
        return {'godzilla': 0, 'mirror': 0, 'total': 0, 'error': str(e)}
    total_count = godzilla_count + mirror_count
    print(f'[BrowserFetcher] Done, total {total_count} records')
    return {
        'godzilla': godzilla_count,
        'mirror': mirror_count,
        'total': total_count,
    }
