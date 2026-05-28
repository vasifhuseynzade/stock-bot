# v4.2 IBKR Read-Only Bridge

This bridge connects to TWS/IB Gateway and serves a read-only JSON snapshot for the Railway Telegram bot.

It does not place orders.

## Local Windows quick start

1. Open TWS live or paper.
2. Enable API socket clients in TWS:
   - Enable ActiveX and Socket Clients: ON
   - Read-Only API: ON
   - Live TWS port: 7496
   - Paper TWS port: 7497
3. Install dependency:

```powershell
py -m pip install ib_insync eventkit nest-asyncio
```

4. Run live TWS bridge:

```powershell
py .\ibkr_bridge_server_py314.py --tws-host 127.0.0.1 --tws-port 7496 --client-id 91 --bind 127.0.0.1 --http-port 8765 --token CHANGE_ME_LONG_RANDOM
```

5. Test locally:

```powershell
curl "http://127.0.0.1:8765/health"
curl "http://127.0.0.1:8765/snapshot?token=CHANGE_ME_LONG_RANDOM"
```

## Railway bot connection

Railway cannot reach `127.0.0.1` on your laptop. For the bot to fetch this bridge, run the bridge on a VPS or expose it through a secure tunnel.

Recommended production path:

- VPS with GUI + IB Gateway
- Bridge bound to localhost
- Reverse proxy / Cloudflare Tunnel / Tailscale Funnel with HTTPS
- Strong token in `IBKR_BRIDGE_TOKEN`

Set Railway variables:

```text
IBKR_RECON_ENABLED=1
IBKR_RECON_AUTO_ENABLED=1
IBKR_RECON_AFTER_CLOSE_MINUTE=970
IBKR_BRIDGE_URL=https://your-secure-bridge-url
IBKR_BRIDGE_TOKEN=your-long-random-token
```

970 = 16:10 New York time in minutes after midnight.

## Important

Do not expose TWS API socket directly to the internet. Only expose this bridge endpoint if protected by HTTPS and a strong token.
