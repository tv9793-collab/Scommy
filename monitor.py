"""
📊 Odds Movement Monitor
Monitora le oscillazioni di quota e invia alert su Telegram.

Campionati scelti: mercati meno liquidi → più errori bookmaker → più oscillazioni.
Finestra attiva: 08:00 – 23:30 ora italiana (riduce chiamate API al minimo).
Partite live e partite fuori orario vengono ignorate.

Consumo stimato API: ~480 chiamate/mese (limite free: 500).
"""

import requests
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROME_TZ = ZoneInfo("Europe/Rome")

# ─── Configurazione ────────────────────────────────────────────────────────────
ODDS_API_KEY        = os.environ["ODDS_API_KEY"]
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]

SOGLIA = float(os.environ.get("SOGLIA_OSCILLAZIONE", "1.5"))

# Campionati con più oscillazioni (mercati meno liquidi, bookmaker più lenti)
# Serie A e top leagues escluse: troppo seguite dai trader sharp, poche sorprese
SPORT = [
    "soccer_italy_serie_b",        # Serie B: meno trader sharp, più errori quota
    "soccer_turkey_super_league",  # Super Lig: mercato sottile, ritardi frequenti
]

# Finestra oraria (ora italiana) in cui ha senso monitorare
ORA_INIZIO  = 8    # 08:00
ORA_FINE    = 23   # 23:30
MIN_FINE    = 30

STATE_FILE = "previous_odds.json"

# ─── Filtri temporali ──────────────────────────────────────────────────────────

def ora_italiana() -> datetime:
    return datetime.now(tz=ROME_TZ)

def fuori_orario() -> bool:
    """True se l'ora attuale è fuori dalla finestra 08:00–23:30 italiana.
    In questo caso lo script termina senza consumare chiamate API."""
    ora = ora_italiana()
    if ora.hour < ORA_INIZIO:
        return True
    if ora.hour > ORA_FINE:
        return True
    if ora.hour == ORA_FINE and ora.minute >= MIN_FINE:
        return True
    return False

def partita_valida(commence_time_str: str) -> bool:
    """True se la partita:
    - Non è ancora iniziata (esclude partite live)
    - Inizia nella finestra 08:00–23:30 ora italiana
    """
    try:
        dt_utc = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        dt_it  = dt_utc.astimezone(ROME_TZ)

        # Scarta partite già iniziate (live)
        if dt_utc <= datetime.now(tz=timezone.utc):
            return False

        # Scarta partite che iniziano dopo le 23:30
        if dt_it.hour > ORA_FINE:
            return False
        if dt_it.hour == ORA_FINE and dt_it.minute >= MIN_FINE:
            return False

        # Scarta partite che iniziano prima delle 08:00
        if dt_it.hour < ORA_INIZIO:
            return False

        return True
    except Exception:
        return True  # parsing fallito: includi per sicurezza

# ─── API Odds ──────────────────────────────────────────────────────────────────

def get_odds(sport: str) -> list:
    """Recupera quote da The Odds API."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    r = requests.get(url, params={
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }, timeout=15)

    if r.status_code == 200:
        rimaste = r.headers.get("x-requests-remaining", "?")
        usate   = r.headers.get("x-requests-used", "?")
        print(f"  [API {sport}] Usate: {usate} | Rimaste: {rimaste}")
        return r.json()

    print(f"  [ERRORE] {sport} → HTTP {r.status_code}: {r.text[:200]}")
    return []

def quota_media(game: dict) -> dict:
    """Calcola quota media su tutti i bookmaker per ogni esito (1/X/2)."""
    totals, counts = {}, {}
    for bk in game.get("bookmakers", []):
        for market in bk.get("markets", []):
            if market["key"] != "h2h":
                continue
            for outcome in market["outcomes"]:
                nome   = outcome["name"]
                prezzo = outcome["price"]
                totals[nome] = totals.get(nome, 0) + prezzo
                counts[nome] = counts.get(nome, 0) + 1
    return {
        nome: round(totals[nome] / counts[nome], 2)
        for nome in totals
        if counts[nome] > 0
    }

# ─── Telegram ──────────────────────────────────────────────────────────────────

def invia_telegram(testo: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": testo,
        "parse_mode": "HTML",
    }, timeout=10)
    if r.status_code != 200:
        print(f"  [TELEGRAM ERRORE] {r.text[:200]}")

# ─── Stato ─────────────────────────────────────────────────────────────────────

def carica_stato() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def salva_stato(stato: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(stato, f, indent=2, ensure_ascii=False)

# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    ora_it = ora_italiana()
    print(f"\n{'='*50}")
    print(f"  ODDS MONITOR — {ora_it.strftime('%H:%M %d/%m/%Y')} (ora italiana)")
    print(f"  Soglia alert: ±{SOGLIA}")
    print(f"{'='*50}\n")

    # Nessuna chiamata API fuori dalla finestra 08:00–23:30
    if fuori_orario():
        print(f"⏸️  Fuori orario ({ora_it.strftime('%H:%M')}). Nessuna chiamata API effettuata.")
        return

    stato_precedente = carica_stato()
    stato_corrente   = {}
    alert_count      = 0
    partite_skip     = 0

    for sport in SPORT:
        print(f"📡 Recupero {sport}...")
        partite = get_odds(sport)
        print(f"   → {len(partite)} partite totali dall'API")

        for game in partite:
            commence = game.get("commence_time", "")

            # Salta partite live e partite fuori orario
            if not partita_valida(commence):
                partite_skip += 1
                continue

            gid   = game["id"]
            home  = game.get("home_team", "?")
            away  = game.get("away_team", "?")
            quote = quota_media(game)
            nome  = f"{home} vs {away}"
            data  = game.get("commence_time", "")[:10]

            stato_corrente[gid] = {
                "nome":  nome,
                "data":  data,
                "quote": quote,
            }

            # Nessun confronto al primo avvio
            if gid not in stato_precedente:
                continue

            vecchie = stato_precedente[gid].get("quote", {})

            for team, nuova in quote.items():
                vecchia = vecchie.get(team)
                if vecchia is None:
                    continue

                delta = abs(nuova - vecchia)
                if delta < SOGLIA:
                    continue

                # ─── ALERT ─────────────────────────────────────────────────
                freccia = "⬆️ SALITA" if nuova > vecchia else "⬇️ DISCESA"
                sport_label = sport.replace("soccer_", "").replace("_", " ").upper()

                messaggio = (
                    f"🚨 <b>OSCILLAZIONE QUOTA</b>\n"
                    f"🏆 {sport_label}\n"
                    f"⚽️ <b>{nome}</b>\n"
                    f"📅 {data}\n"
                    f"─────────────────\n"
                    f"🏷️ <b>{team}</b>\n"
                    f"   Prima:  <b>{vecchia:.2f}</b>\n"
                    f"   Adesso: <b>{nuova:.2f}</b> {freccia}\n"
                    f"   Delta:  <b>{delta:+.2f}</b>\n"
                    f"─────────────────\n"
                    f"🕐 {datetime.now().strftime('%H:%M del %d/%m/%Y')}"
                )

                print(f"\n⚠️  ALERT: {nome} | {team}: {vecchia}→{nuova} ({delta:+.2f})")
                invia_telegram(messaggio)
                alert_count += 1

    salva_stato(stato_corrente)

    # Report finale
    partite_totali = len(stato_corrente)
    print(f"\n  Partite live/fuori orario ignorate: {partite_skip}")
    if stato_precedente:
        print(f"✅ Fatto. Partite monitorate: {partite_totali} | Alert inviati: {alert_count}")
    else:
        print(f"✅ Primo avvio completato. Stato salvato per {partite_totali} partite.")
        invia_telegram(
            f"✅ <b>Odds Monitor avviato</b>\n"
            f"📊 Partite caricate: {partite_totali}\n"
            f"🎯 Soglia alert: ±{SOGLIA}\n"
            f"🏆 Serie B + Super Lig\n"
            f"🕗 Finestra: 08:00 – 23:30\n"
            f"⏱️ Controllo ogni 2 ore\n"
            f"🕐 {ora_it.strftime('%H:%M del %d/%m/%Y')}"
        )

if __name__ == "__main__":
    main()
