# quote-bot

Salva le quote **Pinnacle** (The Odds API v4) delle competizioni monitorate
dal framework scommesse, per due usi:

1. **Riferimento sharp** per calcolare `p_fair` durante l'analisi in chat
   (istantanea del mattino).
2. **Quota di chiusura** per il CLV di giocate e scarti (istantanea presa
   entro 40 minuti dal via).

Non cerca pick e non invia notifiche. Nessuna dipendenza esterna.

## File prodotti

- `data/giorni/AAAA-MM-GG.json`: un file per giorno (data di inizio partita,
  ora di Roma). Per ogni evento: competizione, squadre/giocatori, orario e
  fino a tre istantanee (`mattino`, `chiusura`, `manuale`) con quote
  Pinnacle, `p_fair_pct` (devig Shin, campo `metodo_devig`), `quota_equa`, `margine_pct`,
  `pinnacle_last_update`, `minuti_al_via`.
- `data/stato.json`: crediti residui letti dall'API, giorno dell'ultima
  istantanea del mattino, ultime note di esecuzione.

Lettura da Claude in chat:
`https://raw.githubusercontent.com/tv9793-collab/Scommy/main/data/giorni/AAAA-MM-GG.json`

## Installazione nel repository esistente

1. **Elimina tutti i file del vecchio bot, compresi i vecchi file in
   `.github/workflows/`**: se resta un vecchio workflow continua a girare e
   consuma crediti.
2. Carica questi file mantenendo la struttura delle cartelle.
3. Secret del repository: `ODDS_API_KEY` con la chiave di The Odds API
   (Settings → Secrets and variables → Actions). Se il vecchio secret ha un
   altro nome, crea questo; i secret di Telegram si possono eliminare.
4. Rendi il repository pubblico (serve per la lettura dei file raw). La
   chiave resta protetta: i secret non sono visibili nel codice.
5. Primo avvio: Actions → Quote Pinnacle → Run workflow (modo `mattino`).

## Funzionamento

- Il workflow gira ogni 20 minuti (minuti 7, 27, 47).
- `/events` è gratuito: serve a sapere se ci sono partite prima di spendere
  crediti.
- `/odds` con `bookmakers=pinnacle` e `markets=h2h` costa 1 credito per
  chiamata, e una chiamata copre tutte le partite di quella competizione
  nella finestra.
- Istantanea del mattino: una volta al giorno, primo giro dopo le 07:00 di
  Roma, partite delle prossime 26 ore. Il primo mattino non viene mai
  sovrascritto; un avvio manuale (`mattino`) salva in `manuale`.
- Chiusura: partite che iniziano entro 40 minuti e non hanno ancora la
  chiusura. Solo calcio (e basket quando acceso): l'orario dei match di
  tennis dopo il primo su un campo è stimato.
- Guardia crediti: sotto 60 residui solo calcio, sotto 15 solo chiusure
  calcio. Errori di chiave o crediti esauriti fanno fallire il run (email
  di GitHub).

Stima consumo (calcolo, da verificare con `data/stato.json` dopo il primo
mese): circa 220 crediti al mese per il calcio più circa 20 per torneo di
tennis Masters 1000 / WTA 1000. Il piano gratuito ha 500 crediti al mese.

## Limiti noti

- La "chiusura" è l'ultima quota entro 40 minuti dal via, non la quota
  esatta al fischio d'inizio. Se GitHub ritarda o scarta un giro, una
  partita può restare senza chiusura.
- The Odds API dichiara che le quote Pinnacle arrivano dal sito pubblico e
  possono avere un ritardo.
- Nessun book con licenza ADM (Sisal, Snai, ecc.) è disponibile: il line
  shopping resta manuale.
- Basket spento: accendere `attivo` in `bot/quote_bot.py` quando inizia la
  fase monitorata dal prompt (playoff NBA/WNBA, quarti EuroLeague).
- GitHub disattiva i workflow pianificati dei repository pubblici dopo 60
  giorni senza attività. I commit dei dati dovrebbero contare come attività
  (non verificato): dopo una pausa lunga controlla che il workflow sia attivo.

## Test

```
python -m unittest -v
```
