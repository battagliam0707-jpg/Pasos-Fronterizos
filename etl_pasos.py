name: Actualizar Pasos Fronterizos

on:
  schedule:
    # 15:00 UTC = 12:00 PM hora Argentina (UTC-3)
    - cron: "0 15 * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  extraer:
    runs-on: ubuntu-latest
    steps:
      - name: Clonar repositorio
        uses: actions/checkout@v4

      - name: Configurar Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Instalar dependencias
        run: pip install requests beautifulsoup4

      - name: Ejecutar extracción y consolidado
        run: python etl_pasos.py

      - name: Guardar cambios en CSV
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add *.csv
          git diff --staged --quiet || git commit -m "Actualización diaria pasos fronterizos"
          git push
