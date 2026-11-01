import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot import quote_bot as qb


def evento(eid, inizio, casa="Inter", trasferta="Roma", prezzi=(1.60, 4.20, 5.50), con_pinnacle=True):
    esiti = [{"name": casa, "price": prezzi[0]}]
    if len(prezzi) == 3:
        esiti += [{"name": "Draw", "price": prezzi[1]}, {"name": trasferta, "price": prezzi[2]}]
    else:
        esiti += [{"name": trasferta, "price": prezzi[1]}]
    books = []
    if con_pinnacle:
        books.append({"key": "pinnacle", "markets": [
            {"key": "h2h", "last_update": qb.iso(inizio - timedelta(hours=1)), "outcomes": esiti}]})
    return {"id": eid, "commence_time": qb.iso(inizio), "home_team": casa, "away_team": trasferta,
            "bookmakers": books}


class ClientFinto(qb.ClientOddsApi):
    """Simula The Odds API: eventi per sport_key, filtrati per finestra."""

    def __init__(self, eventi_per_sport, residui=400, errore=None):
        super().__init__("finta")
        self.eventi_per_sport = eventi_per_sport
        self.residui = residui
        self.errore = errore
        self.chiamate = []

    def _get(self, percorso, parametri):
        parti = percorso.strip("/").split("/")
        sport, tipo = parti[1], parti[2]
        self.chiamate.append((tipo, sport))
        if self.errore:
            return self.errore
        if sport not in self.eventi_per_sport:
            return 404, {"error_code": "UNKNOWN_SPORT"}
        da = qb.da_iso(parametri["commenceTimeFrom"])
        a = qb.da_iso(parametri["commenceTimeTo"])
        lista = [e for e in self.eventi_per_sport[sport] if da <= qb.da_iso(e["commence_time"]) <= a]
        if tipo == "odds":
            self.residui -= 1
        self.crediti_residui = self.residui
        if tipo == "events":
            return 200, [{k: e[k] for k in ("id", "commence_time", "home_team", "away_team")} for e in lista]
        return 200, lista


SOLO = {
    "soccer_italy_serie_a": qb.COMPETIZIONI["soccer_italy_serie_a"],
    "tennis_atp_paris_masters": qb.COMPETIZIONI["tennis_atp_paris_masters"],
}


class TestCalcoli(unittest.TestCase):
    def test_devig_esempio_prompt(self):
        p, margine = qb.devig({"1": 1.60, "X": 4.20, "2": 5.50})
        self.assertAlmostEqual(p["1"] * 100, 60.7, places=1)   # esempio del prompt v8.3
        self.assertAlmostEqual(1 / p["1"], 1.647, places=2)
        due, _ = qb.devig({"1": 1.40, "2": 3.10})
        self.assertAlmostEqual(due["1"] * 100, 69.6, places=1)
        self.assertAlmostEqual(sum(p.values()), 1.0, places=9)
        self.assertGreater(margine, 0)

    def test_devig_quote_non_valide(self):
        with self.assertRaises(ValueError):
            qb.devig({"1": 1.0, "2": 3.0})

    def test_istantanea_calcio_e_tennis(self):
        adesso = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
        foto = qb.istantanea(evento("a" * 32, adesso + timedelta(hours=3)), "calcio", adesso)
        self.assertEqual(list(foto["quote"]), ["1", "X", "2"])
        self.assertEqual(foto["minuti_al_via"], 180)
        self.assertAlmostEqual(foto["p_fair_pct"]["1"], 60.7, places=1)
        tennis = qb.istantanea(evento("b" * 32, adesso + timedelta(hours=3), "Sinner", "Zverev", (1.40, 3.10)),
                               "tennis", adesso)
        self.assertEqual(list(tennis["quote"]), ["1", "2"])

    def test_istantanea_dati_incompleti(self):
        adesso = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
        senza_x = evento("c" * 32, adesso + timedelta(hours=3), prezzi=(1.5, 2.6))
        self.assertIsNone(qb.istantanea(senza_x, "calcio", adesso))
        self.assertIsNone(qb.istantanea(evento("d" * 32, adesso, con_pinnacle=False), "calcio", adesso))
        strano = evento("e" * 32, adesso + timedelta(hours=1))
        strano["bookmakers"][0]["markets"][0]["outcomes"][0]["name"] = "Squadra sconosciuta"
        self.assertIsNone(qb.istantanea(strano, "calcio", adesso))


class TestCiclo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def leggi(self, data):
        return json.loads((self.cartella / "giorni" / f"{data}.json").read_text(encoding="utf-8"))

    def test_mattino_una_volta_al_giorno(self):
        adesso = datetime(2026, 10, 24, 6, 10, tzinfo=timezone.utc)  # 08:10 a Roma
        partita = evento("1" * 32, adesso + timedelta(hours=6))
        client = ClientFinto({"soccer_italy_serie_a": [partita]})
        esito = qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        self.assertEqual(esito["chiamate_quote"], 1)
        giorno = self.leggi("2026-10-24")
        self.assertIn("mattino", giorno["eventi"]["1" * 32])
        self.assertEqual(qb.leggi_stato(self.cartella)["mattino_fatto"], "2026-10-24")
        # secondo giro 20 minuti dopo: nessuna nuova chiamata a pagamento
        esito2 = qb.esegui(client, self.cartella, adesso + timedelta(minutes=20), competizioni=SOLO)
        self.assertEqual(esito2["chiamate_quote"], 0)

    def test_niente_mattino_prima_delle_7_di_roma(self):
        adesso = datetime(2026, 10, 24, 4, 30, tzinfo=timezone.utc)  # 06:30 a Roma
        client = ClientFinto({"soccer_italy_serie_a": [evento("2" * 32, adesso + timedelta(hours=8))]})
        esito = qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        self.assertEqual(esito["chiamate_quote"], 0)
        self.assertNotIn("mattino_fatto", qb.leggi_stato(self.cartella))

    def test_chiusura_presa_una_sola_volta(self):
        adesso = datetime(2026, 10, 24, 15, 50, tzinfo=timezone.utc)
        qb.scrivi_stato(self.cartella, {"mattino_fatto": "2026-10-24"})
        partita = evento("3" * 32, adesso + timedelta(minutes=30))
        lontana = evento("4" * 32, adesso + timedelta(hours=3), "Milan", "Lazio")
        client = ClientFinto({"soccer_italy_serie_a": [partita, lontana]})
        esito = qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        self.assertEqual(esito["chiamate_quote"], 1)
        eventi = self.leggi("2026-10-24")["eventi"]
        self.assertEqual(eventi["3" * 32]["chiusura"]["minuti_al_via"], 30)
        self.assertNotIn("4" * 32, eventi)  # fuori finestra di chiusura
        esito2 = qb.esegui(client, self.cartella, adesso + timedelta(minutes=20), competizioni=SOLO)
        self.assertEqual(esito2["chiamate_quote"], 0)

    def test_partita_gia_iniziata_esclusa(self):
        adesso = datetime(2026, 10, 24, 15, 50, tzinfo=timezone.utc)
        qb.scrivi_stato(self.cartella, {"mattino_fatto": "2026-10-24"})
        client = ClientFinto({"soccer_italy_serie_a": [evento("5" * 32, adesso)]})
        esito = qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        self.assertEqual(esito["chiamate_quote"], 0)

    def test_data_file_in_ora_di_roma(self):
        adesso = datetime(2026, 10, 24, 6, 10, tzinfo=timezone.utc)
        notturna = evento("6" * 32, datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc))  # 01:30 del 25 a Roma
        client = ClientFinto({"soccer_italy_serie_a": [notturna]})
        qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        self.assertIn("6" * 32, self.leggi("2026-10-25")["eventi"])

    def test_pochi_crediti_solo_calcio(self):
        adesso = datetime(2026, 10, 24, 6, 10, tzinfo=timezone.utc)
        client = ClientFinto({
            "soccer_italy_serie_a": [evento("7" * 32, adesso + timedelta(hours=5))],
            "tennis_atp_paris_masters": [evento("8" * 32, adesso + timedelta(hours=5), "Sinner", "Zverev", (1.4, 3.1))],
        }, residui=40)
        qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        pagate = [c for c in client.chiamate if c[0] == "odds"]
        self.assertEqual(pagate, [("odds", "soccer_italy_serie_a")])

    def test_crediti_finiti_niente_chiamate(self):
        adesso = datetime(2026, 10, 24, 6, 10, tzinfo=timezone.utc)
        client = ClientFinto({"soccer_italy_serie_a": [evento("9" * 32, adesso + timedelta(minutes=20))]},
                             residui=0)
        esito = qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        self.assertEqual(esito["chiamate_quote"], 0)

    def test_lettura_crediti_vecchia_non_blocca(self):
        adesso = datetime(2026, 11, 2, 15, 50, tzinfo=timezone.utc)
        qb.scrivi_stato(self.cartella, {"mattino_fatto": "2026-11-02", "crediti_residui": 0,
                                        "crediti_letti_utc": "2026-10-30T10:00:00Z"})

        class SenzaHeader(ClientFinto):
            def _get(self, percorso, parametri):
                esito = super()._get(percorso, parametri)
                if "/events" in percorso:
                    self.crediti_residui = None  # /events senza header crediti
                return esito

        client = SenzaHeader({"soccer_italy_serie_a": [evento("b2" * 16, adesso + timedelta(minutes=25))]})
        esito = qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        self.assertEqual(esito["chiamate_quote"], 1)

    def test_torneo_non_attivo_silenzioso(self):
        adesso = datetime(2026, 10, 24, 6, 10, tzinfo=timezone.utc)
        client = ClientFinto({"soccer_italy_serie_a": []})
        esito = qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        self.assertEqual(esito["log"], [])

    def test_chiave_non_valida_blocca(self):
        adesso = datetime(2026, 10, 24, 6, 10, tzinfo=timezone.utc)
        client = ClientFinto({}, errore=(401, {"error_code": "INVALID_KEY"}))
        with self.assertRaises(qb.ErroreChiave):
            qb.esegui(client, self.cartella, adesso, competizioni=SOLO)

    def test_manuale_non_sovrascrive_mattino(self):
        adesso = datetime(2026, 10, 24, 6, 10, tzinfo=timezone.utc)
        client = ClientFinto({"soccer_italy_serie_a": [evento("a1" * 16, adesso + timedelta(hours=6))]})
        qb.esegui(client, self.cartella, adesso, competizioni=SOLO)
        qb.esegui(client, self.cartella, adesso + timedelta(hours=2), modo="mattino", competizioni=SOLO)
        voce = self.leggi("2026-10-24")["eventi"]["a1" * 16]
        self.assertIn("mattino", voce)
        self.assertIn("manuale", voce)
        self.assertNotEqual(voce["mattino"]["preso_utc"], voce["manuale"]["preso_utc"])


if __name__ == "__main__":
    unittest.main()
