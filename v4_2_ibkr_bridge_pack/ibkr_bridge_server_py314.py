from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs


def ensure_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


def safe_contract(contract: Any) -> Dict[str, Any]:
    fields = [
        'secType', 'conId', 'symbol', 'lastTradeDateOrContractMonth', 'strike', 'right',
        'multiplier', 'exchange', 'primaryExchange', 'currency', 'localSymbol', 'tradingClass',
        'includeExpired', 'secIdType', 'secId', 'description', 'issuerId'
    ]
    out: Dict[str, Any] = {}
    for f in fields:
        try:
            out[f] = getattr(contract, f)
        except Exception:
            pass
    return out


def safe_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


class IBKRBridge:
    def __init__(self, tws_host: str, tws_port: int, client_id: int, readonly: bool = True):
        self.tws_host = tws_host
        self.tws_port = tws_port
        self.client_id = client_id
        self.readonly = readonly
        self.lock = threading.Lock()

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            ensure_event_loop()
            from ib_insync import IB
            ib = IB()
            started = time.time()
            try:
                ib.connect(self.tws_host, self.tws_port, clientId=self.client_id, readonly=self.readonly, timeout=15)
                accounts = ib.managedAccounts()
                selected = accounts[0] if accounts else None
                account_summary = []
                account_values = []
                portfolio = []
                positions = []
                open_orders = []
                executions = []
                try:
                    for v in ib.accountSummary():
                        account_summary.append([v.account, v.tag, v.value, v.currency, v.modelCode])
                except Exception:
                    pass
                try:
                    for v in ib.accountValues():
                        account_values.append([v.account, v.tag, v.value, v.currency, v.modelCode])
                except Exception:
                    pass
                try:
                    for p in ib.portfolio():
                        portfolio.append({
                            'account': p.account,
                            'contract': safe_contract(p.contract),
                            'position': safe_float(p.position),
                            'marketPrice': safe_float(p.marketPrice),
                            'marketValue': safe_float(p.marketValue),
                            'averageCost': safe_float(p.averageCost),
                            'unrealizedPNL': safe_float(p.unrealizedPNL),
                            'realizedPNL': safe_float(p.realizedPNL),
                        })
                except Exception:
                    pass
                try:
                    for p in ib.positions():
                        positions.append({
                            'account': p.account,
                            'contract': safe_contract(p.contract),
                            'position': safe_float(p.position),
                            'avgCost': safe_float(p.avgCost),
                        })
                except Exception:
                    pass
                try:
                    for t in ib.openTrades():
                        order = t.order
                        status = t.orderStatus
                        open_orders.append({
                            'contract': safe_contract(t.contract),
                            'orderId': getattr(order, 'orderId', None),
                            'permId': getattr(order, 'permId', None),
                            'action': getattr(order, 'action', None),
                            'orderType': getattr(order, 'orderType', None),
                            'totalQuantity': safe_float(getattr(order, 'totalQuantity', 0)),
                            'lmtPrice': safe_float(getattr(order, 'lmtPrice', 0)),
                            'status': getattr(status, 'status', None),
                            'filled': safe_float(getattr(status, 'filled', 0)),
                            'remaining': safe_float(getattr(status, 'remaining', 0)),
                        })
                except Exception:
                    pass
                try:
                    # Recent executions from current session only unless TWS provides more.
                    for f in ib.fills():
                        executions.append({
                            'contract': safe_contract(f.contract),
                            'execution': {
                                'execId': getattr(f.execution, 'execId', None),
                                'time': str(getattr(f.execution, 'time', '')),
                                'acctNumber': getattr(f.execution, 'acctNumber', None),
                                'side': getattr(f.execution, 'side', None),
                                'shares': safe_float(getattr(f.execution, 'shares', 0)),
                                'price': safe_float(getattr(f.execution, 'price', 0)),
                                'orderId': getattr(f.execution, 'orderId', None),
                                'permId': getattr(f.execution, 'permId', None),
                            },
                            'commissionReport': {
                                'commission': safe_float(getattr(f.commissionReport, 'commission', 0)),
                                'currency': getattr(f.commissionReport, 'currency', None),
                                'realizedPNL': safe_float(getattr(f.commissionReport, 'realizedPNL', 0)),
                            } if getattr(f, 'commissionReport', None) else {},
                        })
                except Exception:
                    pass
                return {
                    'probe_version': 'v4_2_bridge_py314_readonly',
                    'created_utc': datetime.now(timezone.utc).isoformat(),
                    'runtime_seconds': round(time.time() - started, 3),
                    'connection': {
                        'host': self.tws_host,
                        'port': self.tws_port,
                        'client_id': self.client_id,
                        'readonly': self.readonly,
                        'account_selected': selected,
                    },
                    'managed_accounts': accounts,
                    'account_summary': account_summary,
                    'account_values': account_values,
                    'portfolio': portfolio,
                    'positions': positions,
                    'open_orders': open_orders,
                    'executions': executions,
                }
            finally:
                try:
                    ib.disconnect()
                except Exception:
                    pass


def make_handler(bridge: IBKRBridge, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'IBKRBridge/0.1'

        def _send(self, code: int, body: Dict[str, Any]) -> None:
            raw = json.dumps(body, indent=2, default=str).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if not token:
                return True
            qs = parse_qs(urlparse(self.path).query)
            got = self.headers.get('X-IBKR-Bridge-Token') or (qs.get('token', [''])[0])
            return got == token

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == '/health':
                return self._send(200, {'ok': True, 'service': 'ibkr_bridge', 'time_utc': datetime.now(timezone.utc).isoformat()})
            if path != '/snapshot':
                return self._send(404, {'ok': False, 'error': 'not found'})
            if not self._authorized():
                return self._send(401, {'ok': False, 'error': 'unauthorized'})
            try:
                data = bridge.snapshot()
                return self._send(200, data)
            except Exception as exc:
                return self._send(500, {'ok': False, 'error': str(exc), 'traceback': traceback.format_exc()})

        def log_message(self, fmt: str, *args: Any) -> None:
            print('%s - - [%s] %s' % (self.client_address[0], self.log_date_time_string(), fmt % args))

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description='Read-only IBKR bridge HTTP server for the Telegram bot.')
    parser.add_argument('--tws-host', default='127.0.0.1')
    parser.add_argument('--tws-port', type=int, default=7496)
    parser.add_argument('--client-id', type=int, default=91)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--http-port', type=int, default=8765)
    parser.add_argument('--token', default='')
    parser.add_argument('--allow-orders', action='store_true', help='Not used in v4.2; bridge still connects readonly unless changed in source.')
    args = parser.parse_args()

    bridge = IBKRBridge(args.tws_host, args.tws_port, args.client_id, readonly=True)
    handler = make_handler(bridge, args.token)
    httpd = ThreadingHTTPServer((args.bind, args.http_port), handler)
    print(f'IBKR read-only bridge listening on http://{args.bind}:{args.http_port}')
    print(f'TWS/Gateway target {args.tws_host}:{args.tws_port}, clientId={args.client_id}, readonly=True')
    if args.token:
        print('Token protection enabled. Use ?token=... or X-IBKR-Bridge-Token header.')
    else:
        print('WARNING: no token set. Use only on localhost/private network.')
    httpd.serve_forever()


if __name__ == '__main__':
    main()
