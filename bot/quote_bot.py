"""Bot quote Pinnacle per il framework scommesse.

Scarica da The Odds API (v4) le quote Pinnacle 1X2 / testa a testa delle
competizioni monitorate dal prompt e le salva nel repository, un file JSON
per giorno (data di inizio partita, ora di Roma):

    data/giorni/AAAA-MM-GG.json

Per ogni evento il file contiene fino a tre istantanee:
  - "mattino":   una volta al giorno, alla prima esecuzione dopo ORA_MATTINO
                 (ora di Roma), per tutte le partite delle prossime
                 FINESTRA_MATTINO_ORE ore. E' la quota di riferimento sharp
                 per calcolare p_fair durante l'analisi in chat.
  - "chiusura":  l'ultima quota presa nei FINESTRA_CHIUSURA_MIN minuti prima
                 del calcio d'inizio. E' l'approssimazione della quota di
                 chiusura per il CLV.
  - "manuale":   istantanea forzata a mano (workflow_dispatch, modo mattino).

Per ogni istantanea: quote Pinnacle, p_fair (devig proporzionale, stessa
formula del prompt), quota equa = 1/p_fair, margine del book.

Il bot NON cerca pick e non invia notifiche: fornisce solo dati.
Solo libreria standard Python: nessuna dipendenza da installare.

Consumo crediti (documentazione ufficiale v4): /events e' gratuito,
/odds costa mercati x regioni; con bookmakers=pinnacle e markets=h2h
ogni chiamata /odds costa 1 credito. Il bot chiama /odds solo per le
competizioni che hanno davvero partite nella finestra (verificato prima
con /events, gratuito).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

API_BASE = "https://api.the-odds-api.com/v4"
ROMA = ZoneInfo("Europe/Rome")
BOOKMAKER = "pinnacle"
MERCATO = "h2h"

ORA_MATTINO = 7               # ora di Roma da cui parte l'istantanea del mattino
FINESTRA_MATTINO_ORE = 26     # partite coperte dall'istantanea del mattino
FINESTRA_CHIUSURA_MIN = 40    # minuti prima del via in cui si prende la chiusura

# Soglie di sicurezza sui crediti residui (letti dall'header
# x-requests-remaining dell'ultima chiamata /odds).
RISERVA_SOLO_CALCIO = 60      # sotto questa soglia: solo calcio
RISERVA_SOLO_CHIUSURA = 15    # sotto questa soglia: solo chiusura calcio

# Competizioni monitorate dal prompt (Step 1). Le chiavi sono quelle
# ufficiali di The Odds API.
#   gruppo:   priorita' di spesa crediti (calcio > tennis > basket)
#   attivo:   se False la competizione viene ignorata
#   chiusura: se True si prende anche l'istantanea di chiusura
# Basket: il prompt monitora NBA/WNBA solo nei playoff ed EuroLeague solo
# dai quarti in poi. Il bot non sa in che fase si e' (lo dice la lista
# eventi, non la chiave), quindi sono spenti: si accendono a mano qui
# quando inizia la fase giusta.
# Tennis: chiusura spenta perche' l'orario di inizio dei match dopo il
# primo su un campo e' solo stimato, e la chiusura costerebbe molti crediti.
COMPETIZIONI: dict[str, dict] = {
    "soccer_italy_serie_a": {"nome": "Serie A", "gruppo": "calcio", "attivo": True, "chiusura": True},
    "soccer_epl": {"nome": "Premier League", "gruppo": "calcio", "attivo": True, "chiusura": True},
    "soccer_spain_la_liga": {"nome": "La Liga", "gruppo": "calcio", "attivo": True, "chiusura": True},
    "soccer_germany_bundesliga": {"nome": "Bundesliga", "gruppo": "calcio", "attivo": True, "chiusura": True},
    "soccer_france_ligue_one": {"nome": "Ligue 1", "gruppo": "calcio", "attivo": True, "chiusura": True},
    "soccer_uefa_champs_league": {"nome": "Champions League", "gruppo": "calcio", "attivo": True, "chiusura": True},
    "soccer_uefa_europa_league": {"nome": "Europa League", "gruppo": "calcio", "attivo": True, "chiusura": True},
    "soccer_uefa_europa_conference_league": {"nome": "Conference League", "gruppo": "calcio", "attivo": True, "chiusura": True},
    # Tennis: Slam, Masters 1000 ATP, WTA 1000
    "tennis_atp_aus_open_singles": {"nome": "ATP Australian Open", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_indian_wells": {"nome": "ATP Indian Wells", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_miami_open": {"nome": "ATP Miami", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_monte_carlo_masters": {"nome": "ATP Monte-Carlo", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_madrid_open": {"nome": "ATP Madrid", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_italian_open": {"nome": "ATP Roma", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_french_open": {"nome": "ATP Roland Garros", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_wimbledon": {"nome": "ATP Wimbledon", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_canadian_open": {"nome": "ATP Canada", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_cincinnati_open": {"nome": "ATP Cincinnati", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_us_open": {"nome": "ATP US Open", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_shanghai_masters": {"nome": "ATP Shanghai", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_atp_paris_masters": {"nome": "ATP Parigi", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_aus_open_singles": {"nome": "WTA Australian Open", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_qatar_open": {"nome": "WTA Doha", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_dubai": {"nome": "WTA Dubai", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_indian_wells": {"nome": "WTA Indian Wells", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_miami_open": {"nome": "WTA Miami", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_madrid_open": {"nome": "WTA Madrid", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_italian_open": {"nome": "WTA Roma", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_french_open": {"nome": "WTA Roland Garros", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_wimbledon": {"nome": "WTA Wimbledon", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_canadian_open": {"nome": "WTA Canada", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_cincinnati_open": {"nome": "WTA Cincinnati", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_us_open": {"nome": "WTA US Open", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_china_open": {"nome": "WTA Pechino", "gruppo": "tennis", "attivo": True, "chiusura": False},
    "tennis_wta_wuhan_open": {"nome": "WTA Wuhan", "gruppo": "tennis", "attivo": True, "chiusura": False},
    # Basket: accendere a mano nella fase monitorata dal prompt
    "basketball_nba": {"nome": "NBA", "gruppo": "basket", "attivo": False, "chiusura": True},
    "basketball_wnba": {"nome": "WNBA", "gruppo": "basket", "attivo": False, "chiusura": True},
    "basketball_euroleague": {"nome": "EuroLeague", "gruppo": "basket", "attivo": False, "chiusura": True},
}

ORDINE_GRUPPI = {"calcio": 0, "tennis": 1, "basket": 2}


class ErroreChiave(Exception):
    """Chiave API mancante, non valida o crediti finiti: il run deve fallire."""


# ---------------------------------------------------------------------------
# Accesso API
# ---------------------------------------------------------------------------

class ClientOddsApi:
    def __init__(self, api_key: str, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout
        self.crediti_residui: int | None = None
        self.crediti_usati: int | None = None

    def _get(self, percorso: str, parametri: dict) -> tuple[int, object]:
        parametri = dict(parametri, apiKey=self.api_key)
        url = f"{API_BASE}{percorso}?{urllib.parse.urlencode(parametri)}"
        richiesta = urllib.request.Request(url, headers={"User-Agent": "quote-bot/1.0"})
        try:
            with urllib.request.urlopen(richiesta, timeout=self.timeout) as risposta:
                self._leggi_header(risposta.headers)
                return risposta.status, json.loads(risposta.read().decode("utf-8"))
        except urllib.error.HTTPError as errore:
            self._leggi_header(errore.headers)
            try:
                corpo = json.loads(errore.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                corpo = {}
            return errore.code, corpo

    def _leggi_header(self, header) -> None:
        if header is None:
            return
        residui = header.get("x-requests-remaining")
        usati = header.get("x-requests-used")
        if residui is not None:
            try:
                self.crediti_residui = int(float(residui))
            except ValueError:
                pass
        if usati is not None:
            try:
                self.crediti_usati = int(float(usati))
            except ValueError:
                pass

    def eventi(self, sport_key: str, da: datetime, a: datetime) -> tuple[int, object]:
        """Lista eventi (gratuita, nessuna quota)."""
        return self._get(
            f"/sports/{sport_key}/events",
            {"dateFormat": "iso", "commenceTimeFrom": iso(da), "commenceTimeTo": iso(a)},
        )

    def quote(self, sport_key: str, da: datetime, a: datetime) -> tuple[int, object]:
        """Quote Pinnacle h2h (1 credito)."""
        return self._get(
            f"/sports/{sport_key}/odds",
            {
                "bookmakers": BOOKMAKER,
                "markets": MERCATO,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": iso(da),
                "commenceTimeTo": iso(a),
            },
        )


# ---------------------------------------------------------------------------
# Utilita' date e calcoli
# ---------------------------------------------------------------------------

def iso(momento: datetime) -> str:
    """Formato richiesto dall'API: 2026-10-24T08:00:00Z (senza microsecondi)."""
    return momento.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def da_iso(testo: str) -> datetime:
    return datetime.fromisoformat(testo.replace("Z", "+00:00")).astimezone(timezone.utc)


def data_roma(momento: datetime) -> str:
    return momento.astimezone(ROMA).strftime("%Y-%m-%d")


def ora_roma(momento: datetime) -> str:
    return momento.astimezone(ROMA).strftime("%Y-%m-%d %H:%M")


METODO_DEVIG = "shin"


def devig(quote: dict[str, float]) -> tuple[dict[str, float], float]:
    """Metodo Shin, stessa formula del prompt (v8.3, Step 1).

    pi_i = 1/q_i, B = somma(pi_j)
    p_i(z) = (sqrt(z^2 + 4(1-z) pi_i^2 / B) - z) / (2(1-z))
    con z in [0, 0.5) tale che somma(p_i) = 1 (bisezione).
    Ritorna (p_fair, margine %).
    """
    inverse = {esito: 1.0 / q for esito, q in quote.items() if q and q > 1.0}
    if len(inverse) != len(quote) or len(inverse) < 2:
        raise ValueError(f"quote non valide per il devig: {quote}")
    somma = sum(inverse.values())
    if somma <= 1.0:
        # nessun margine (o arbitraggio): Shin non applicabile, normalizzo
        return {e: v / somma for e, v in inverse.items()}, (somma - 1.0) * 100.0

    def prob(z: float) -> dict[str, float]:
        return {e: (math.sqrt(z * z + 4 * (1 - z) * v * v / somma) - z) / (2 * (1 - z))
                for e, v in inverse.items()}

    basso, alto = 0.0, 0.5
    for _ in range(100):
        z = (basso + alto) / 2
        if sum(prob(z).values()) > 1.0:
            basso = z
        else:
            alto = z
    p_fair = prob((basso + alto) / 2)
    totale = sum(p_fair.values())
    p_fair = {e: v / totale for e, v in p_fair.items()}  # residuo numerico
    return p_fair, (somma - 1.0) * 100.0


def istantanea(evento: dict, gruppo: str, adesso: datetime) -> dict | None:
    """Estrae le quote Pinnacle h2h di un evento e calcola p_fair.

    Ritorna None se Pinnacle o il mercato non sono presenti.
    """
    casa, trasferta = evento.get("home_team"), evento.get("away_team")
    for book in evento.get("bookmakers", []):
        if book.get("key") != BOOKMAKER:
            continue
        for mercato in book.get("markets", []):
            if mercato.get("key") != MERCATO:
                continue
            quote_nomi: dict[str, float] = {}
            for esito in mercato.get("outcomes", []):
                quote_nomi[esito.get("name")] = float(esito.get("price"))
            segni: dict[str, float] = {}
            for nome, prezzo in quote_nomi.items():
                if nome == casa:
                    segni["1"] = prezzo
                elif nome == trasferta:
                    segni["2"] = prezzo
                elif nome == "Draw":
                    segni["X"] = prezzo
                else:
                    return None  # nome esito inatteso: meglio nessun dato che un dato sbagliato
            attesi = {"1", "X", "2"} if gruppo == "calcio" else {"1", "2"}
            if set(segni) != attesi:
                return None
            p_fair, margine = devig(segni)
            inizio = da_iso(evento["commence_time"])
            return {
                "preso_utc": iso(adesso),
                "preso_roma": ora_roma(adesso),
                "minuti_al_via": round((inizio - adesso).total_seconds() / 60),
                "pinnacle_last_update": mercato.get("last_update") or book.get("last_update"),
                "quote": {s: round(segni[s], 3) for s in sorted(segni, key="1X2".index)},
                "p_fair_pct": {s: round(p_fair[s] * 100, 2) for s in sorted(p_fair, key="1X2".index)},
                "quota_equa": {s: round(1.0 / p_fair[s], 3) for s in sorted(p_fair, key="1X2".index)},
                "margine_pct": round(margine, 2),
            }
    return None


# ---------------------------------------------------------------------------
# Archivio su file
# ---------------------------------------------------------------------------

class Archivio:
    def __init__(self, cartella: Path):
        self.cartella = Path(cartella)
        self.giorni_dir = self.cartella / "giorni"
        self.giorni_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._modificati: set[str] = set()

    def giorno(self, data: str) -> dict:
        if data not in self._cache:
            percorso = self.giorni_dir / f"{data}.json"
            if percorso.exists():
                self._cache[data] = json.loads(percorso.read_text(encoding="utf-8"))
            else:
                self._cache[data] = {
                    "data": data,
                    "fonte": "The Odds API v4, bookmaker pinnacle, mercato h2h (1X2 calcio, testa a testa tennis/basket)",
                    "metodo_devig": METODO_DEVIG,
                    "note": "p_fair con devig Shin; chiusura = ultima quota presa entro "
                            f"{FINESTRA_CHIUSURA_MIN} minuti dal via (approssimazione della chiusura)",
                    "eventi": {},
                }
        return self._cache[data]

    def evento(self, ev: dict, sport_key: str) -> dict:
        inizio = da_iso(ev["commence_time"])
        giorno = self.giorno(data_roma(inizio))
        voce = giorno["eventi"].setdefault(ev["id"], {})
        voce.update({
            "competizione": COMPETIZIONI[sport_key]["nome"],
            "sport_key": sport_key,
            "inizio_utc": iso(inizio),
            "inizio_roma": ora_roma(inizio),
            "casa": ev.get("home_team"),
            "trasferta": ev.get("away_team"),
        })
        self._modificati.add(data_roma(inizio))
        return voce

    def cerca(self, evento_id: str, inizio: datetime) -> dict | None:
        return self.giorno(data_roma(inizio))["eventi"].get(evento_id)

    def salva(self, adesso: datetime) -> list[str]:
        scritti = []
        for data in sorted(self._modificati):
            contenuto = self._cache[data]
            contenuto["aggiornato_utc"] = iso(adesso)
            contenuto["eventi"] = dict(sorted(
                contenuto["eventi"].items(), key=lambda kv: (kv[1].get("inizio_utc", ""), kv[0])
            ))
            percorso = self.giorni_dir / f"{data}.json"
            percorso.write_text(json.dumps(contenuto, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            scritti.append(str(percorso))
        self._modificati.clear()
        return scritti


def leggi_stato(cartella: Path) -> dict:
    percorso = Path(cartella) / "stato.json"
    if percorso.exists():
        return json.loads(percorso.read_text(encoding="utf-8"))
    return {}


def scrivi_stato(cartella: Path, stato: dict) -> None:
    percorso = Path(cartella) / "stato.json"
    percorso.write_text(json.dumps(stato, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Logica principale
# ---------------------------------------------------------------------------

def gruppo_ammesso(gruppo: str, residui: int | None, tipo: str) -> bool:
    """Guardia sui crediti: con pochi crediti si tiene solo il calcio."""
    if residui is None:
        return True
    if residui <= 0:
        return False
    if residui < RISERVA_SOLO_CHIUSURA:
        return gruppo == "calcio" and tipo == "chiusura"
    if residui < RISERVA_SOLO_CALCIO:
        return gruppo == "calcio"
    return True


def _controlla_errore(stato_http: int, corpo: object, contesto: str, log: list[str]) -> bool:
    """True se la risposta e' utilizzabile. Solleva ErroreChiave sugli errori bloccanti."""
    if stato_http == 200:
        return True
    codice = corpo.get("error_code") if isinstance(corpo, dict) else None
    if codice in {"MISSING_KEY", "INVALID_KEY", "DEACTIVATED_KEY", "OUT_OF_USAGE_CREDITS"} or stato_http == 401:
        raise ErroreChiave(f"{contesto}: HTTP {stato_http} {codice or ''}".strip())
    if codice == "UNKNOWN_SPORT" or stato_http == 404:
        return False  # torneo non in corso: chiave non attiva, nessun log
    log.append(f"{contesto}: HTTP {stato_http} {codice or ''}".strip())
    return False


def esegui(client: ClientOddsApi, cartella: Path, adesso: datetime, modo: str = "auto",
           competizioni: dict[str, dict] | None = None) -> dict:
    """Un ciclo del bot. modo: auto | mattino | chiusura."""
    competizioni = competizioni if competizioni is not None else COMPETIZIONI
    cartella = Path(cartella)
    archivio = Archivio(cartella)
    stato = leggi_stato(cartella)
    log: list[str] = []
    chiamate_quote = 0

    oggi = data_roma(adesso)
    ora_locale = adesso.astimezone(ROMA).hour
    fai_mattino = modo == "mattino" or (
        modo == "auto" and ora_locale >= ORA_MATTINO and stato.get("mattino_fatto") != oggi
    )
    fai_chiusura = modo in {"auto", "chiusura"}
    chiave_mattino = "manuale" if modo == "mattino" else "mattino"
    residui = stato.get("crediti_residui")
    letti = stato.get("crediti_letti_utc")
    if residui is not None and (not letti or adesso - da_iso(letti) > timedelta(hours=24)):
        # lettura vecchia (es. dopo il rinnovo mensile): si riparte da "sconosciuto"
        # cosi' il bot non resta bloccato su un valore a zero non piu' vero
        residui = None

    attive = sorted(
        (k for k, v in competizioni.items() if v.get("attivo")),
        key=lambda k: (ORDINE_GRUPPI.get(competizioni[k]["gruppo"], 9), k),
    )

    for sport_key in attive:
        conf = competizioni[sport_key]
        gruppo = conf["gruppo"]

        # --- istantanea del mattino (riferimento sharp per l'analisi) ---
        if fai_mattino:
            fine = adesso + timedelta(hours=FINESTRA_MATTINO_ORE)
            stato_http, eventi = client.eventi(sport_key, adesso, fine)  # gratuita
            residui = client.crediti_residui if client.crediti_residui is not None else residui
            if (_controlla_errore(stato_http, eventi, f"eventi {sport_key}", log) and eventi
                    and gruppo_ammesso(gruppo, residui, "mattino")):
                stato_http, dati = client.quote(sport_key, adesso, fine)
                chiamate_quote += 1
                residui = client.crediti_residui if client.crediti_residui is not None else residui
                if _controlla_errore(stato_http, dati, f"quote mattino {sport_key}", log):
                    salvati = 0
                    for ev in dati:
                        foto = istantanea(ev, gruppo, adesso)
                        if foto is None:
                            continue
                        voce = archivio.evento(ev, sport_key)
                        if chiave_mattino == "mattino" and "mattino" in voce:
                            continue  # il mattino resta quello del primo giro
                        voce[chiave_mattino] = foto
                        salvati += 1
                    log.append(f"{chiave_mattino} {sport_key}: {salvati} eventi")

        # --- istantanea di chiusura ---
        if fai_chiusura and conf.get("chiusura"):
            fine = adesso + timedelta(minutes=FINESTRA_CHIUSURA_MIN)
            stato_http, eventi = client.eventi(sport_key, adesso, fine)  # gratuita
            residui = client.crediti_residui if client.crediti_residui is not None else residui
            if not _controlla_errore(stato_http, eventi, f"eventi chiusura {sport_key}", log) or not eventi:
                continue
            if not gruppo_ammesso(gruppo, residui, "chiusura"):
                log.append(f"chiusura {sport_key}: saltata, crediti residui {residui}")
                continue
            mancanti = []
            for ev in eventi:
                inizio = da_iso(ev["commence_time"])
                if inizio <= adesso:
                    continue
                gia = archivio.cerca(ev["id"], inizio)
                if gia and "chiusura" in gia:
                    continue
                mancanti.append(ev["id"])
            if not mancanti:
                continue
            stato_http, dati = client.quote(sport_key, adesso, fine)
            chiamate_quote += 1
            residui = client.crediti_residui if client.crediti_residui is not None else residui
            if not _controlla_errore(stato_http, dati, f"quote chiusura {sport_key}", log):
                continue
            salvati = 0
            for ev in dati:
                if ev.get("id") not in mancanti:
                    continue
                foto = istantanea(ev, gruppo, adesso)
                if foto is None:
                    continue
                archivio.evento(ev, sport_key)["chiusura"] = foto
                salvati += 1
            log.append(f"chiusura {sport_key}: {salvati} eventi")

    scritti = archivio.salva(adesso)
    if fai_mattino and modo == "auto":
        stato["mattino_fatto"] = oggi
    # Battito per Claude: ultimo_run_utc arrotondato all'ora, cosi' il file
    # cambia al massimo una volta l'ora (niente commit ogni 20 minuti) e
    # l'analisi in chat capisce se il bot e' fermo (prompt v8.3).
    stato["ultimo_run_utc"] = iso(adesso.replace(minute=0, second=0, microsecond=0))
    # Il resto dello stato si riscrive solo se cambia qualcosa di utile.
    if client.crediti_residui is not None and (
            client.crediti_residui != stato.get("crediti_residui") or chiamate_quote):
        stato["crediti_residui"] = client.crediti_residui
        stato["crediti_letti_utc"] = iso(adesso)
    if client.crediti_usati is not None and client.crediti_usati != stato.get("crediti_usati_mese"):
        stato["crediti_usati_mese"] = client.crediti_usati
    if log:
        stato["ultime_note"] = [f"{iso(adesso)} {riga}" for riga in log][-20:]
    scrivi_stato(cartella, stato)
    return {"chiamate_quote": chiamate_quote, "file": scritti, "log": log, "stato": stato}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bot quote Pinnacle per il framework scommesse")
    parser.add_argument("--modo", choices=["auto", "mattino", "chiusura"], default="auto")
    parser.add_argument("--cartella", default="data")
    args = parser.parse_args(argv)

    chiave = os.environ.get("ODDS_API_KEY", "").strip()
    if not chiave:
        print("ERRORE: variabile ODDS_API_KEY mancante (secret del repository)", file=sys.stderr)
        return 1
    try:
        esito = esegui(ClientOddsApi(chiave), Path(args.cartella), datetime.now(timezone.utc), args.modo)
    except ErroreChiave as errore:
        print(f"ERRORE BLOCCANTE: {errore}", file=sys.stderr)
        return 1
    for riga in esito["log"]:
        print(riga)
    print(f"chiamate /odds: {esito['chiamate_quote']}, crediti residui: {esito['stato'].get('crediti_residui')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
