"""
Feishu push service for efficiency dashboard
"""
import json
import requests
from datetime import date
from services.statistics import get_group_ranking, get_today_overview, get_active_groups


def test_feishu_webhook(webhook_url):
    if not webhook_url:
        return False, 'Webhook URL cannot be empty'
    test_msg = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "Feishu Push Test"},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "Webhook configured successfully!"
                    }
                }
            ]
        }
    }
    try:
        resp = requests.post(webhook_url, json=test_msg, timeout=10)
        data = resp.json()
        if data.get('code') == 0 or data.get('StatusCode') == 0:
            return True, 'Push test successful'
        else:
            return False, f'Push failed: {data.get("msg", "unknown error")}'
    except Exception as e:
        return False, f'Request error: {str(e)}'


def send_feishu_card(db, webhook_url):
    try:
        overview = get_today_overview(db)
        groups = get_active_groups(db)
        elements = []
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Today Overview ({overview['date']})**\n"
                           f"Total Finish: **{overview['total_finish']}**  |  "
                           f"Total Hours: **{overview['total_work_hours']}** h\n"
                           f"Avg Efficiency: **{overview['avg_efficiency']}** pcs/h  |  "
                           f"Achievement: **{overview['achievement_rate']}%**"
            }
        })
        elements.append({"tag": "hr"})
        for g in overview['groups']:
            color_icon = 'OK' if g['color_class'] == 'green' else ('!' if g['color_class'] == 'yellow' else 'X')
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"{color_icon} **{g['name']}**  finish {g['total_finish']}  "
                               f"eff {g['avg_efficiency']} pcs/h  "
                               f"ach {g['achievement_rate']}%"
                }
            })
        elements.append({"tag": "hr"})
        for g_name in groups:
            ranking = get_group_ranking(db, g_name, top_n=5)
            if not ranking:
                continue
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**TOP 5 - {g_name}**"}
            })
            for i, p in enumerate(ranking, 1):
                medal = f'{i}.'
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"{medal} **{p['name']}**  eff {p['avg_efficiency']} pcs/h  "
                                   f"ach {p['achievement_rate']}%  finish {p['total_finish']}"
                    }
                })
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"Updated: {date.today().strftime('%Y-%m-%d %H:%M')}  "
                               f"| Efficiency Dashboard"
                }
            ]
        })
        if overview['achievement_rate'] >= 100:
            template = "green"
        elif overview['achievement_rate'] >= 90:
            template = "yellow"
        else:
            template = "red"
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"Efficiency Dashboard - {overview['date']}"
                    },
                    "template": template
                },
                "elements": elements
            }
        }
        resp = requests.post(webhook_url, json=card, timeout=10)
        data = resp.json()
        if data.get('code') == 0 or data.get('StatusCode') == 0:
            return True, 'Push successful'
        else:
            return False, f'Push failed: {data.get("msg", str(data))}'
    except Exception as e:
        return False, f'Push error: {str(e)}'
