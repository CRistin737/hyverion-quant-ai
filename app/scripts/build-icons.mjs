// Rebuild every raster brand asset from the SVG masters in assets/brand/.
//   pnpm icons
// Outputs: assets/hyverion-quant-ai-icon.png (1024), assets/hyverion-quant-ai-mark.png (512),
// assets/hyverion-quant-ai-icon.icns (via iconutil) and the Tauri icon set (src-tauri/icons).
import { Resvg } from "@resvg/resvg-js";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const brand = join(repo, "assets", "brand");

function render(svgFile, size, target) {
  const svg = readFileSync(join(brand, svgFile), "utf8");
  const png = new Resvg(svg, { fitTo: { mode: "width", value: size }, background: "rgba(0,0,0,0)" }).render().asPng();
  writeFileSync(target, png);
}

render("hyverion-icon.svg", 1024, join(repo, "assets", "hyverion-quant-ai-icon.png"));
render("hyverion-mark.svg", 512, join(repo, "assets", "hyverion-quant-ai-mark.png"));

const iconset = join(mkdtempSync(join(tmpdir(), "hyverion-")), "hyverion.iconset");
execFileSync("mkdir", ["-p", iconset]);
for (const size of [16, 32, 128, 256, 512]) {
  render("hyverion-icon.svg", size, join(iconset, `icon_${size}x${size}.png`));
  render("hyverion-icon.svg", size * 2, join(iconset, `icon_${size}x${size}@2x.png`));
}
execFileSync("iconutil", ["-c", "icns", iconset, "-o", join(repo, "assets", "hyverion-quant-ai-icon.icns")]);
rmSync(dirname(iconset), { recursive: true, force: true });

execFileSync("pnpm", ["tauri", "icon", join(repo, "assets", "hyverion-quant-ai-icon.png")], { cwd: join(repo, "app"), stdio: "ignore" });
// macOS-only product: drop the mobile and Windows Store variants tauri generates.
const icons = join(repo, "app", "src-tauri", "icons");
for (const extra of ["android", "ios", "icon.ico", "StoreLogo.png"]) rmSync(join(icons, extra), { recursive: true, force: true });
for (const name of readdirSync(icons)) if (name.startsWith("Square")) rmSync(join(icons, name));
console.log("brand assets rebuilt");
