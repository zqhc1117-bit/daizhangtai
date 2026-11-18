"""Read-only monthly bookkeeping progress from the default actual balance table."""

import re

from db import ApiError


def balance_progress(conn, period):
    """Batch all companies using the balance table's opening and active journal sources.

    Empty chart-of-account rows do not count. Opening balances and previous
    months' net amounts carry forward. Any actual current-month movement counts
    even if the ending balance (or opposite signed movements) nets to zero.
    Draft invoices, attachments and unposted classifications are excluded just
    as they are in the default actual-account balance view.
    """
    if not isinstance(period, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
        raise ApiError(400, '余额表进度期间无效')
    rows = conn.execute('''
        WITH sources AS (
            SELECT client_id, account_code, debit_fen-credit_fen AS opening_fen,
                   0 AS current_activity
            FROM opening_balances
            UNION ALL
            SELECT v.client_id, l.account_code,
                   SUM(CASE WHEN v.period < :period THEN l.debit_fen-l.credit_fen ELSE 0 END),
                   MAX(CASE WHEN v.period = :period AND (l.debit_fen<>0 OR l.credit_fen<>0) THEN 1 ELSE 0 END)
            FROM accounting_voucher_lines l
            JOIN vouchers v ON v.id=l.voucher_id AND v.client_id=l.client_id
            WHERE v.status='active' AND v.period<=:period
            GROUP BY v.client_id, l.account_code
        ), amounts AS (
            SELECT s.client_id, s.account_code, SUM(s.opening_fen) AS opening_fen,
                   MAX(s.current_activity) AS current_activity
            FROM sources s
            JOIN accounts a ON a.client_id=s.client_id AND a.code=s.account_code
            GROUP BY s.client_id, s.account_code
        ), populated AS (
            SELECT client_id, COUNT(*) AS account_count
            FROM amounts
            WHERE opening_fen<>0 OR current_activity<>0
            GROUP BY client_id
        )
        SELECT c.id AS client_id,
               CASE WHEN :period>=COALESCE(NULLIF(b.opening_period,''),c.start_period)
                    THEN COALESCE(p.account_count,0) ELSE 0 END AS account_count
        FROM clients c
        LEFT JOIN book_settings b ON b.client_id=c.id
        LEFT JOIN populated p ON p.client_id=c.id
        WHERE c.deleted=0
        ORDER BY c.id
    ''', {'period': period})
    return [{'client_id': row['client_id'], 'period': period,
             'has_data': row['account_count'] > 0, 'account_count': row['account_count']}
            for row in rows]
