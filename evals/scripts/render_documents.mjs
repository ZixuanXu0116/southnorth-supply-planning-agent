// Render the supplier-document test set (evals/src/snm_evals/documents.py) with Chromium:
//   node evals/scripts/render_documents.mjs evals/documents/v1
// pdf: a PDF with a text layer; png: a screenshot; scan: the screenshot printed into a PDF (no text layer).
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

// CommonJS resolution, so a globally installed Playwright is found through NODE_PATH.
const { chromium } = createRequire(import.meta.url)("playwright");

const dir = resolve(process.argv[2] ?? "evals/documents/v1");
const manifest = JSON.parse(readFileSync(join(dir, "render.json"), "utf8"));
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 860, height: 1000 }, deviceScaleFactor: 1.5 });
for (const item of manifest) {
	await page.goto(pathToFileURL(join(dir, item.html)).href);
	if (item.format === "pdf") {
		await page.pdf({ path: join(dir, item.out), format: "A4", printBackground: true });
	} else {
		const shot = await page.screenshot({ fullPage: true });
		if (item.format === "png") {
			await page.screenshot({ path: join(dir, item.out), fullPage: true });
		} else {
			const scan = await browser.newPage();
			await scan.setContent(
				`<html><body style="margin:0"><img style="width:100%" src="data:image/png;base64,${shot.toString("base64")}"></body></html>`,
			);
			await scan.pdf({ path: join(dir, item.out), format: "A4" });
			await scan.close();
		}
	}
	console.log(item.out);
}
await browser.close();
