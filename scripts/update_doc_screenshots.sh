#!/usr/bin/env bash
# Copy verified visual baselines (1440x900) into the documentation gallery.
# Run `cd app && pnpm test:visual` first so the baselines match the current UI.
set -euo pipefail
cd "$(dirname "$0")/.."
src=app/tests/visual/__screenshots__
dst=docs/ui/capturas
for name in inicio trading-mercado trading-operaciones trading-informe noticias-calendario noticias-noticias \
  noticias-fuentes inteligencia-agentes inteligencia-historial inteligencia-modelos inteligencia-aprendizaje \
  inteligencia-memoria riesgo-limites riesgo-decisiones ajustes ajustes-salud; do
  cp "$src/$name-1440x900.png" "$dst/$name.png"
done
cp "$src/inicio-1440x900-light.png" "$dst/inicio-claro.png"
cp "$src/onboarding-1440x900.png" "$dst/onboarding.png"
echo "Capturas actualizadas en $dst"
