"""
abdavinci warehouse performance data fetcher
- Playwright + CAS SSO login
- Fetch warehouse personal performance data from abdavinci share dashboard
"""
import os
import json
import re
from datetime import datetime, date

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sync_playwright = None
    PWTimeout = Exception


DEFAULT_ABDAVINCI_SHARE = (
    "https://abdavinci.aihuishou.com/share.html#share/dashboard"
    "?shareInfo=DA1C3E7DAF7EC46FDC39861F3AB261FC"
)
DEFAULT_CAS_URL = "https://sso.aihuishou.com/cas/login"


def cas_login(page, username, password, target_url=None):
    if not username or not password:
        print('[abdavinci] CAS username/password empty, skip')
        return False
    try:
        url = target_url or DEFAULT_CAS_URL
        page.goto(url, wait_until='networkidle', timeout=30000)
        if 'cas/login' not in page.url and 'login' not in page.url.lower():
            print('[abdavinci] Already logged in')
            return True
        print(f'[abdavinci] CAS login page: {page.url[:80]}')
        page.fill('#username', username)
        page.fill('#password', password)
        try:
            page.click("button[name='submitBtn']")
        except:
            try:
                page.click("button[type='submit']")
            except:
                page.click("input[type='submit']")
        page.wait_for_load_state('networkidle', timeout=30000)
        if 'cas/login' in page.url or 'login' in page.url.lower():
            print('[abdavinci] CAS login failed')
            return False
        print(f'[abdavinci] CAS login success: {page.url[:80]}')
        return True
    except PWTimeout:
        print('[abdavinci] CAS login timeout')
        return False
    except Exception as e:
        print(f'[abdavinci] CAS login error: {e}')
        return False


def fetch_warehouse_performance(page, target_date=None):
    if target_date is None:
        target_date = date.today().strftime('%Y-%m-%d')
    result = {
        'staff_list': [],
        'records': [],
        'groups': [],
    }
    try:
        print(f'[abdavinci] Fetch {target_date} warehouse performance - TODO')
    except Exception as e:
        print(f'[abdavinci] Fetch error: {e}')
    return result


def sync_staff_to_db(db, staff_list):
    added = 0
    updated = 0
    for staff in staff_list:
        staff_no = staff.get('staff_no', '').strip()
        name = staff.get('name', '').strip()
        group_name = staff.get('group_name', 'Ungrouped').strip()
        target = float(staff.get('target_efficiency', 100))
        if not staff_no or not name:
            continue
        row = db.execute(
            'SELECT id, name, group_name FROM staff WHERE staff_no=?',
            (staff_no,)
        ).fetchone()
        if row:
            if row['name'] != name or row['group_name'] != group_name:
                db.execute(
                    'UPDATE staff SET name=?, group_name=?, target_efficiency=? WHERE staff_no=?',
                    (name, group_name, target, staff_no)
                )
                updated += 1
        else:
            db.execute(
                'INSERT INTO staff(staff_no, name, group_name, target_efficiency, status) '
                'VALUES(?,?,?,?,1)',
                (staff_no, name, group_name, target)
            )
            added += 1
    if added > 0 or updated > 0:
        db.commit()
    print(f'[abdavinci] Staff sync: +{added}, ~{updated}, total {len(staff_list)}')
    return {'added': added, 'updated': updated, 'total': len(staff_list)}


def sync_records_to_db(db, records, record_date, data_source='abdavinci'):
    count = 0
    for rec in records:
        staff_no = rec.get('staff_no', '').strip()
        hour = int(rec.get('hour', 0))
        finish_count = int(rec.get('finish_count', 0))
        work_hours = float(rec.get('work_hours', 0))
        efficiency = float(rec.get('efficiency', 0))
        if not staff_no or finish_count <= 0:
            continue
        db.execute(
            'DELETE FROM efficiency_record WHERE record_date=? AND hour=? AND staff_no=?',
            (record_date, hour, staff_no)
        )
        db.execute(
            'INSERT INTO efficiency_record(record_date, hour, staff_no, finish_count, '
            'work_hours, efficiency, data_source) VALUES(?,?,?,?,?,?,?)',
            (record_date, hour, staff_no, finish_count, work_hours, efficiency, data_source)
        )
        count += 1
    if count > 0:
        db.commit()
    print(f'[abdavinci] Records synced: {count} ({record_date})')
    return count


def load_config(db):
    config = {}
    keys = [
        'cas_username', 'cas_password',
        'abdavinci_share_url', 'abdavinci_data_api',
    ]
    for key in keys:
        row = db.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
        config[key] = row['value'] if row else ''
    return config


def fetch_and_sync(db, target_date=None, config=None):
    if sync_playwright is None:
        print('[abdavinci] Playwright not installed, skip')
        return {'error': 'playwright_not_installed'}
    if config is None:
        config = load_config(db)
    username = config.get('cas_username', '')
    password = config.get('cas_password', '')
    share_url = config.get('abdavinci_share_url', DEFAULT_ABDAVINCI_SHARE)
    if not username or not password:
        print('[abdavinci] CAS not configured, skip')
        return {'error': 'cas_not_configured'}
    result = {
        'staff_added': 0,
        'staff_updated': 0,
        'records_synced': 0,
        'groups': [],
    }
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                ]
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
            )
            page = context.new_page()
            print('[abdavinci] Step 1: CAS login...')
            login_ok = cas_login(page, username, password, target_url=share_url)
            if not login_ok:
                browser.close()
                result['error'] = 'cas_login_failed'
                return result
            print('[abdavinci] Step 2: Fetch warehouse performance...')
            data = fetch_warehouse_performance(page, target_date)
            if data['staff_list']:
                print('[abdavinci] Step 3: Sync staff list...')
                staff_result = sync_staff_to_db(db, data['staff_list'])
                result['staff_added'] = staff_result['added']
                result['staff_updated'] = staff_result['updated']
            if data['records']:
                print('[abdavinci] Step 4: Sync efficiency records...')
                count = sync_records_to_db(db, data['records'], target_date or date.today().strftime('%Y-%m-%d'))
                result['records_synced'] = count
            result['groups'] = data.get('groups', [])
            browser.close()
    except Exception as e:
        print(f'[abdavinci] Error: {e}')
        result['error'] = str(e)
    print(f'[abdavinci] Done: staff +{result["staff_added"]} '
          f'~{result["staff_updated"]} records {result["records_synced"]}')
    return result
