# Guía del mantenedor

Esta guía es para ti, el dueño del proyecto (**@CRistin737**). Explica cómo
configurar el repositorio público `CRistin737/hyverion-quant-ai` en GitHub para
que **nadie pueda cambiar `main` sin tu aprobación**, cómo proteger tu correo y
qué significa la licencia Apache-2.0.

Haz estos pasos una sola vez, justo después de crear el repositorio y subir el
primer commit.

---

## 1. Proteger la rama `main`

**Settings › Rules › Rulesets › New ruleset › New branch ruleset**
(o, en la vista clásica, **Settings › Branches › Add branch protection rule**).

1. **Ruleset name:** `main protegida`. **Enforcement status:** `Active`.
2. **Target branches:** `Add target › Include default branch` (o el patrón `main`).
3. Activa **Restrict deletions** (nadie puede borrar `main`).
4. Activa **Block force pushes** (nadie puede reescribir el historial).
5. Activa **Require a pull request before merging** y dentro:
   - **Required approvals:** `1`.
   - **Dismiss stale pull request approvals when new commits are pushed.**
   - **Require review from Code Owners.** Con el archivo
     `.github/CODEOWNERS` (`* @CRistin737`) esto significa que **tu aprobación es
     obligatoria** en todo el repositorio.
   - **Require conversation resolution before merging.**
6. Activa **Require status checks to pass** y añade los checks
   `python`, `app` y `macos` (aparecen en la lista después de la primera
   ejecución de CI). Marca **Require branches to be up to date before merging**.
7. **Bypass list (opcional):** si te añades a ti mismo como *Repository admin*
   podrás fusionar en una emergencia sin esperar a CI. Si prefieres que las
   reglas también se apliquen a ti (más seguro), deja la lista vacía. En la vista
   clásica esto es la casilla **Do not allow bypassing the above settings**
   ("Include administrators").

Nota: como eres el único code owner, GitHub no te deja aprobar tus propios PR.
Para tus cambios tienes dos opciones: fusionarlos usando el bypass del paso 7, o
trabajar directamente con PR y fusionar como administrador. Los PR de otras
personas siempre necesitarán tu aprobación.

## 2. Forma de fusionar

**Settings › General › Pull Requests**

- Deja marcada solo **Allow squash merging** (desmarca *merge commits* y
  *rebase merging*). Cada PR entra como un único commit limpio.
- Marca **Always suggest updating pull request branches** y
  **Automatically delete head branches**.
- Opcional: **Allow auto-merge** desmarcado, para que nada se fusione sin que tú
  pulses el botón.

## 3. GitHub Actions para colaboradores externos

**Settings › Actions › General**

- **Actions permissions:** `Allow all actions and reusable workflows` o, mejor,
  `Allow CRistin737, and select non-CRistin737, actions and reusable workflows`
  y permite `actions/*`, `github/*`, `astral-sh/*`, `pnpm/*`, `dtolnay/*`,
  `Swatinem/*`, `softprops/*`.
- **Fork pull request workflows from outside collaborators:**
  `Require approval for all outside collaborators`. Así el código de un
  desconocido no se ejecuta en tus runners hasta que tú lo revises.
- **Workflow permissions:** `Read repository contents and packages permissions`
  y desmarca **Allow GitHub Actions to create and approve pull requests**.

## 4. Seguridad del código

**Settings › Security › Advanced Security** (antes "Code security and analysis"):

- **Private vulnerability reporting:** `Enable`. Es el botón
  "Report a vulnerability" que citan `SECURITY.md` y las plantillas de issues.
- **Dependency graph:** `Enable`.
- **Dependabot alerts:** `Enable`.
- **Dependabot security updates:** `Enable`. Las actualizaciones semanales ya
  están configuradas en `.github/dependabot.yml`; cada una llega como un PR que
  tú apruebas.
- **Secret scanning:** `Enable`.
- **Push protection:** `Enable`. GitHub bloquea cualquier push que contenga una
  clave reconocible.
- **Code scanning:** ya queda configurado por `.github/workflows/codeql.yml`
  (CodeQL para Python y TypeScript, en cada PR y cada semana).

## 5. Página web (GitHub Pages)

**Settings › Pages › Build and deployment › Source:** `GitHub Actions`.

El workflow `.github/workflows/pages.yml` publica la carpeta `site/` cada vez que
cambia en `main` (o cuando lo lanzas a mano desde la pestaña Actions). La web
queda en <https://cristin737.github.io/hyverion-quant-ai>.

## 6. Publicar una versión de la app

Pestaña **Actions › Release › Run workflow**, escribe la versión (por ejemplo
`0.1.0`). El workflow compila la app de macOS y crea un **borrador** de release
`v0.1.0` con el `.dmg`. Revisa el borrador y pulsa **Publish release**.

La app **no está firmada ni notarizada** por Apple; las notas de la versión lo
avisan y explican cómo abrirla.

## 7. Proteger tu correo electrónico

Tu correo personal no debe aparecer en commits públicos.

1. **GitHub › Settings (de tu cuenta) › Emails:**
   - Marca **Keep my email addresses private**.
   - Marca **Block command line pushes that expose my email**.
   - Copia tu dirección no-reply (tiene la forma
     `146782337+CRistin737@users.noreply.github.com`).
2. En la carpeta del repositorio público (solo en ese repositorio):

   ```bash
   git config user.name "Cristian Santos"
   git config user.email 146782337+CRistin737@users.noreply.github.com
   ```

3. Antes de subir algo, comprueba que nada personal se cuela:

   ```bash
   uv run python scripts/privacy_check.py
   git log --format='%an <%ae>' | sort -u   # solo debe salir la dirección no-reply
   ```

---

## 8. La licencia Apache-2.0 explicada en sencillo

El código se publica bajo la **Licencia Apache 2.0** (archivo `LICENSE`).

**Lo que otras personas pueden hacer:**

- Usar el código gratis, también con fines comerciales.
- Copiarlo, modificarlo y crear versiones propias (forks).
- Redistribuirlo, con o sin cambios, en código fuente o compilado.

**Lo que tienen que cumplir:**

- Incluir una copia de la licencia y conservar los avisos de copyright y el
  archivo `NOTICE` (donde aparece tu nombre y las atribuciones de terceros, como
  TradingView Lightweight Charts).
- Indicar claramente qué archivos han modificado.
- Aceptar que el software se entrega **sin garantía**: tú no respondes de
  pérdidas ni de fallos (secciones 7 y 8 de la licencia).

**Lo que sigue siendo tuyo:**

- **El copyright.** Publicar con Apache-2.0 no es regalar la autoría: el código
  sigue siendo "Copyright 2026 Cristian Santos". Das permiso de uso, no la
  propiedad.
- **La marca.** La licencia **no** concede el nombre "Hyverion" ni el logo
  (sección 6). Quien haga un fork y lo distribuya debe cambiarle el nombre; ver
  `TRADEMARKS.md`.
- **La decisión de qué entra en tu repositorio.** Cualquiera puede copiar el
  código a su propio fork, pero **nadie puede cambiar tu repositorio** sin que tú
  apruebes y fusiones su pull request (pasos 1 a 3). Tú decides qué se acepta y
  qué no, sin tener que justificarlo.

**Contribuciones de otras personas:** según la sección 5 de la licencia, lo que
alguien te envía en un PR queda bajo Apache-2.0 automáticamente. Además, pedimos
que cada commit lleve `Signed-off-by` (DCO, `git commit -s`), con lo que la
persona certifica que tiene derecho a aportar ese código. El contribuidor conserva
el copyright de sus líneas, pero te concede una licencia amplia y permanente para
usarlas.

**Patentes:** la licencia incluye una concesión de patentes de cada contribuidor
y la retira automáticamente a quien demande alegando que el proyecto infringe una
patente.

Esto no es asesoramiento legal; para dudas concretas consulta a un abogado.
