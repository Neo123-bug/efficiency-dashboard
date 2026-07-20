"""
Statistics service for efficiency dashboard
"""
from datetime import date, datetime


def get_active_groups(db):
    rows = db.execute(
        'SELECT DISTINCT group_name FROM staff WHERE status=1 ORDER BY group_name'
    ).fetchall()
    return [row['group_name'] for row in rows]


def get_all_groups(db):
    rows = db.execute(
        'SELECT DISTINCT group_name FROM staff ORDER BY group_name'
    ).fetchall()
    return [row['group_name'] for row in rows]


def get_today_overview(db):
    today = date.today().strftime('%Y-%m-%d')
    groups = get_active_groups(db)
    result = {
        'total_finish': 0,
        'total_work_hours': 0,
        'avg_efficiency': 0,
        'achievement_rate': 0,
        'staff_count': 0,
        'groups': [],
        'date': today,
    }
    all_eff = []
    for g in groups:
        row = db.execute(
            'SELECT total_finish, total_work_hours, avg_efficiency, achievement_rate '
            'FROM daily_summary WHERE summary_date=? AND group_name=?',
            (today, g)
        ).fetchone()
        staff_count = db.execute(
            'SELECT COUNT(*) as cnt FROM staff WHERE group_name=? AND status=1', (g,)
        ).fetchone()['cnt']
        if row:
            total_finish = row['total_finish']
            total_hours = row['total_work_hours']
            avg_eff = row['avg_efficiency']
            ach_rate = row['achievement_rate']
        else:
            total_finish = 0
            total_hours = 0
            avg_eff = 0
            ach_rate = 0
        result['total_finish'] += total_finish
        result['total_work_hours'] += total_hours
        if avg_eff > 0:
            all_eff.append(avg_eff)
        result['groups'].append({
            'name': g,
            'key': g,
            'total_finish': total_finish,
            'total_work_hours': round(total_hours, 1),
            'avg_efficiency': round(avg_eff, 1),
            'achievement_rate': round(ach_rate, 1),
            'staff_count': staff_count,
            'color_class': _rate_color_class(ach_rate),
        })
    if all_eff:
        result['avg_efficiency'] = round(sum(all_eff) / len(all_eff), 1)
    rates = [g['achievement_rate'] for g in result['groups'] if g['achievement_rate'] > 0]
    if rates:
        result['achievement_rate'] = round(sum(rates) / len(rates), 1)
    result['staff_count'] = db.execute(
        'SELECT COUNT(*) as cnt FROM staff WHERE status=1'
    ).fetchone()['cnt']
    result['total_work_hours'] = round(result['total_work_hours'], 1)
    result['color_class'] = _rate_color_class(result['achievement_rate'])
    return result


def get_group_ranking(db, group_name, top_n=None):
    today = date.today().strftime('%Y-%m-%d')
    exclude_hours = [12]
    staff_rows = db.execute(
        'SELECT staff_no, name, target_efficiency FROM staff '
        'WHERE group_name=? AND status=1 ORDER BY staff_no',
        (group_name,)
    ).fetchall()
    ranking = []
    for staff in staff_rows:
        staff_no = staff['staff_no']
        target = staff['target_efficiency']
        placeholders = ','.join('?' * len(exclude_hours))
        records = db.execute(
            f'SELECT hour, finish_count, work_hours, efficiency '
            f'FROM efficiency_record '
            f'WHERE record_date=? AND staff_no=? AND hour NOT IN ({placeholders}) '
            f'ORDER BY hour',
            (today, staff_no, *exclude_hours)
        ).fetchall()
        total_finish = sum(r['finish_count'] for r in records)
        total_hours = sum(r['work_hours'] for r in records)
        avg_eff = total_finish / total_hours if total_hours > 0 else 0
        achievement = (avg_eff / target * 100) if target > 0 and avg_eff > 0 else 0
        ranking.append({
            'staff_no': staff_no,
            'name': staff['name'],
            'target_efficiency': target,
            'total_finish': total_finish,
            'total_work_hours': round(total_hours, 1),
            'avg_efficiency': round(avg_eff, 1),
            'achievement_rate': round(achievement, 1),
            'color_class': _rate_color_class(achievement),
        })
    ranking.sort(key=lambda x: x['achievement_rate'], reverse=True)
    if ranking and ranking[0]['achievement_rate'] > 0:
        ranking[0]['is_top'] = True
        for i in range(1, len(ranking)):
            ranking[i]['is_top'] = False
    else:
        for r in ranking:
            r['is_top'] = False
    if top_n:
        ranking = ranking[:top_n]
    return ranking


def get_hourly_trend(db):
    today = date.today().strftime('%Y-%m-%d')
    exclude_hours = [12]
    hours = [h for h in range(8, 22) if h not in exclude_hours]
    trend = []
    for h in hours:
        records = db.execute(
            'SELECT finish_count, work_hours FROM efficiency_record '
            'WHERE record_date=? AND hour=?',
            (today, h)
        ).fetchall()
        total_finish = sum(r['finish_count'] for r in records)
        total_hours = sum(r['work_hours'] for r in records)
        eff = total_finish / total_hours if total_hours > 0 else 0
        trend.append({
            'hour': h,
            'label': f'{h}:00',
            'total_finish': total_finish,
            'total_work_hours': round(total_hours, 1),
            'efficiency': round(eff, 1),
        })
    return trend


def recalc_daily_summary(db, target_date):
    groups = get_all_groups(db)
    exclude_hours = [12]
    for g in groups:
        staff_rows = db.execute(
            'SELECT staff_no, target_efficiency FROM staff WHERE group_name=? AND status=1',
            (g,)
        ).fetchall()
        if not staff_rows:
            continue
        total_finish = 0
        total_hours = 0
        eff_list = []
        for staff in staff_rows:
            staff_no = staff['staff_no']
            target = staff['target_efficiency']
            placeholders = ','.join('?' * len(exclude_hours))
            records = db.execute(
                f'SELECT finish_count, work_hours FROM efficiency_record '
                f'WHERE record_date=? AND staff_no=? AND hour NOT IN ({placeholders})',
                (target_date, staff_no, *exclude_hours)
            ).fetchall()
            s_finish = sum(r['finish_count'] for r in records)
            s_hours = sum(r['work_hours'] for r in records)
            s_eff = s_finish / s_hours if s_hours > 0 else 0
            total_finish += s_finish
            total_hours += s_hours
            if s_eff > 0:
                eff_list.append(s_eff / target * 100 if target > 0 else 0)
        avg_eff = total_finish / total_hours if total_hours > 0 else 0
        ach_rate = sum(eff_list) / len(eff_list) if eff_list else 0
        db.execute(
            'INSERT INTO daily_summary(summary_date, group_name, total_finish, total_work_hours, '
            'avg_efficiency, achievement_rate) VALUES(?,?,?,?,?,?) '
            'ON CONFLICT(summary_date, group_name) DO UPDATE SET '
            'total_finish=excluded.total_finish, '
            'total_work_hours=excluded.total_work_hours, '
            'avg_efficiency=excluded.avg_efficiency, '
            'achievement_rate=excluded.achievement_rate',
            (target_date, g, total_finish, round(total_hours, 2),
             round(avg_eff, 2), round(ach_rate, 2))
        )
    db.commit()


def _rate_color_class(rate):
    if rate >= 100:
        return 'green'
    elif rate >= 90:
        return 'yellow'
    elif rate > 0:
        return 'red'
    else:
        return 'gray'


GROUP_COLOR_SCHEMES = [
    {'from': 'primary-600', 'to': 'primary-700', 'bg': 'primary', 'text': 'primary-600'},
    {'from': 'blue-600', 'to': 'blue-700', 'bg': 'blue', 'text': 'blue-600'},
    {'from': 'purple-600', 'to': 'purple-700', 'bg': 'purple', 'text': 'purple-600'},
    {'from': 'emerald-600', 'to': 'emerald-700', 'bg': 'emerald', 'text': 'emerald-600'},
    {'from': 'amber-600', 'to': 'amber-700', 'bg': 'amber', 'text': 'amber-600'},
    {'from': 'rose-600', 'to': 'rose-700', 'bg': 'rose', 'text': 'rose-600'},
    {'from': 'cyan-600', 'to': 'cyan-700', 'bg': 'cyan', 'text': 'cyan-600'},
    {'from': 'violet-600', 'to': 'violet-700', 'bg': 'violet', 'text': 'violet-600'},
]


def get_group_color(index):
    return GROUP_COLOR_SCHEMES[index % len(GROUP_COLOR_SCHEMES)]
