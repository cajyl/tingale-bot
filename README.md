# tingale-bot

Bot de prévision pour les tournois **Metaculus FutureEval** (saison d'automne 2026 et MiniBench).
Il fait partie du projet *augustin-gale* : une expérience où une IA est aux commandes et où un
humain n'intervient que pour l'identité, les comptes et la signature.

**augustingale** (n. f.) : toute méthode pour gagner de l'argent avec une IA aux commandes.

## Règles que ce bot respecte

- **Aucun humain dans la boucle** : les prévisions sont produites et publiées par le code,
  à heures fixes, via GitHub Actions. Personne ne prévoit à la main sur le compte du bot.
- **Raisonnement publié** avec chaque prévision, comme l'exige le règlement du tournoi.
- **Code ouvert** : ce dépôt est public. Toute amélioration par rapport au gabarit officiel
  est décrite ci-dessous et visible dans l'historique git.
- **Conçu et maintenu avec l'assistance d'une IA** (Claude), qui écrit le code et analyse
  les résultats ; l'humain (une SASU française) fournit le compte et les clés.

## Origine

Fork manuel du gabarit officiel [Metaculus/metac-bot-template](https://github.com/Metaculus/metac-bot-template)
(fichiers `main.py`, `bot_helpers.py`, workflows). Le README d'origine, avec la marche à suivre
complète, est conservé dans [`README-template.md`](README-template.md).

Différences avec le gabarit, à ce stade :

1. Workflow « Metaculus Cup » retiré : les bots n'y sont pas éligibles aux prix, il ne ferait que
   consommer des crédits.
2. Garde-fou : sans jeton ni clé de modèle, le workflow s'arrête avant toute installation.
3. Cache des dépendances entre deux exécutions.
4. README remplacé par celui-ci.

Les améliorations du modèle de prévision viendront ensuite, une à une, chacune avec sa raison et,
si possible, sa mesure.

## Secrets attendus (GitHub → Settings → Secrets and variables → Actions)

- `METACULUS_TOKEN` : jeton du compte bot.
- `OPENROUTER_API_KEY` (crédits gratuits du tournoi) **ou** `ANTHROPIC_API_KEY`.

## Workflows

- `Test Bot` : déclenchement manuel, prévoit sur le tournoi de test `bot-testing-area`.
- `Forecast on new AI tournament questions` : toutes les 20 minutes, tournoi d'automne + MiniBench,
  en sautant les questions déjà prévues.
