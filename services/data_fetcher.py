"""
Data fetcher service for efficiency dashboard
"""
import random
import sqlite3
from datetime import datetime


def _get_data_source(group_name):
    if 'Mirror' in group_name or 'mirror' in group_name.lower():
        return 'mirror'
    else:
        return 'godzilla'


def fetch_hour_data(db, target_date, hour):
    if hour == 12:
        return 0
    staff_rows = db.execute(
        'SELECT staff_no, name, group_name, target_efficiency FROM staff WHERE status=1'
    ).fetchall()
    if not staff_rows:
        return 0
    count = 0
    for staff in staff_rows:
        group = staff['group_name']
        target = staff['target_efficiency']
        source = _get_data_source(group)
        if random.random() > 0.8:
            continue
        if source == 'godzilla':
            finish = random.randint(80, 150)
            work_hours = random.choice([0.5, 1.0])
        else:
            finish = random.randint(50, 100)
            work_hours = random.choice([0.5, 1.0])
        efficiency = finish / work_hours if work_hours > 0 else 0
        try:
            db.execute(
                'INSERT INTO efficiency_record(record_date, hour, staff_no, finish_count, '
                'work_hours, efficiency, data_source) VALUES(?,?,?,?,?,?,?)',
                (target_date, hour, staff['staff_no'], finish,
                 work_hours, round(efficiency, 1), source)
            )
            count += 1
        except Exception as e:
            print(f'Insert failed {staff["staff_no"]}: {e}')
    if count > 0:
        db.commit()
    return count


def fetch_from_godzilla(date_str, hour):
    return []


def fetch_from_mirror(date_str, hour):
    return []
