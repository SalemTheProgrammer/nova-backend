# Nova Opsis Tunicotex — backend

Application FastAPI. Elle contient :

- l'agent LangGraph et ses outils (catégorie **Atelier Tunicotex**, documents,
  notifications, planification d'envois, navigation) ;
- la couche de données DIVA, `app/integrations/divatex/` : le seul endroit qui
  interroge la base de production, en lecture seule ;
- les rapports PDF R1 à R5 et les envois planifiés (rapport du matin) ;
- l'authentification par téléphone (code WhatsApp) et l'administration.

Voir le [README racine](../README.md) pour l'ensemble (architecture, écrans, API),
[../docs/tunicotex/calibration.md](../docs/tunicotex/calibration.md) pour l'état de
la calibration et [../docs/tunicotex/provision.sql](../docs/tunicotex/provision.sql)
pour le provisionnement MariaDB.

## Lancer

```bash
pip install -r requirements.txt
cp .env.example .env     # OPENAI_API_KEY, ADMIN_PHONE, AUTH_SECRET, DATABASE_URL, DIVATEX_URL
uvicorn app.main:app --reload --port 8000     # un seul worker
```

La base `nova` est **neuve** : les tables sont créées au démarrage, sans
migration, et les anciens fichiers SQLite de la version pharmaceutique ne
doivent pas être réutilisés. Sans `DIVATEX_URL`, le backend démarre mais les
routes `/tunicotex` répondent 503 « Base DIVA injoignable. ».

## Réglages

Source de vérité : `app/core/config.py`. Modèle : `.env.example`.

| Groupe | Variables |
|---|---|
| Application | `APP_NAME`, `ENVIRONMENT`, `LOG_LEVEL`, `API_PREFIX`, `HOST`, `PORT` |
| Bases | `DATABASE_URL` (MariaDB `nova` en production), `CHAT_HISTORY_DB_PATH` (SQLite) |
| DIVA | `DIVATEX_URL`, `DIVATEX_STATEMENT_TIMEOUT_S` (15), `DIVATEX_CACHE_TTL_S` (60), `DIVATEX_DATE_REFERENCE` (auto) |
| Calibration | `DIVATEX_TEMPS_FACTEUR` (1.0), `DIVATEX_APPLIQUER_MAJORATION` (true), `DIVATEX_DUREE_POSTE_MIN` (480), `DIVATEX_PHASE_FINALE` (12), `DIVATEX_CALIBRE` (false) |
| Sécurité | `CORS_ORIGINS`, `API_KEYS`, `AUTH_SECRET`, `AUTH_TOKEN_TTL_S`, `VERIFICATION_CODE_TTL_S`, `ADMIN_PHONE` |
| LLM et voix | `OPENAI_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `EMBEDDING_MODEL`, `AGENT_RECURSION_LIMIT`, `STT_MODEL`, `TTS_MODEL`, `TTS_VOICE`, `VISION_MODEL` |
| Documents | `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_CLOUD`, `PINECONE_REGION`, `PINECONE_EMBEDDING_DIMENSION`, `DOCUMENTS_PDF_DIR` |
| Notifications | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `WHATSAPP_SERVICE_URL`, `WHATSAPP_INBOUND_ENABLED`, `WHATSAPP_ALLOWED_NUMBERS` |
| Rapport du matin | `AUTO_BILAN_ENABLED`, `AUTO_BILAN_CANAL`, `AUTO_BILAN_DESTINATAIRE`, `AUTO_BILAN_HEURES`, `AUTO_BILAN_RAPPORT` (R1 à R4) |

## Règles à respecter

- **DIVA est en lecture seule.** Le SQL vers `divatex` n'existe que dans
  `app/integrations/divatex/` ; les paramètres sont toujours liés (`:nom`) ; chaque
  connexion exécute `SET SESSION TRANSACTION READ ONLY` et
  `SET SESSION max_statement_time`. Pas d'outil texte-vers-SQL.
- Toute requête sur `lectures` filtre d'abord sur un préfixe d'index
  (`IDChaineMontage, Date`, `IDOFabrication, Date`, `Date` ou `IDEmploye`).
- Sur `employe`, seules les colonnes `IDEmploye, Matricule, Nom, Prenom,
  IDChaineMontage, IDPosteTravail` sont lues.
- « Aujourd'hui » est `date_reference()`, jamais `date.today()`. Toute réponse
  chiffrée et toute page de PDF affichent « Données au JJ/MM/AAAA ».
- Le rendement est suivi de « (estimé) » tant que `DIVATEX_CALIBRE=false`.
- Un seul worker uvicorn : le planificateur d'envois tourne dans le processus.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest -q                 # sans MariaDB, OpenAI ni réseau
python -m pytest -m divatex -v      # optionnel, contre la vraie base DIVA (DIVATEX_URL)
python -m pytest -m mariadb         # optionnel, MariaDB vide (NOVA_TEST_MARIADB_URL)
```

## Calibration

```bash
python -m scripts.calibration_divatex 2026-08-26 2026-08-27 2026-08-28
```

Affiche, par jour et par ligne, la sortie, l'objectif, l'effectif, les minutes
produites, la présence et le rendement, à comparer avec la fiche de production
de Tunicotex (voir `docs/tunicotex/calibration.md`).
