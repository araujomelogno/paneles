/* Genera el PDF del manual de usuario.
 *
 *   npm install playwright-core pdf-lib
 *   node docs/manual/generar_pdf.mjs
 *
 * Portada y cuerpo se imprimen por separado —la portada va a sangre, el cuerpo
 * con márgenes y pie de página— y después se pegan. Chromium aplica una sola
 * configuración de página por impresión, así que no hay forma de hacerlo en
 * una sola pasada.
 *
 * Las capturas no se rehacen acá: para eso está `capturar.mjs`.
 */
import { chromium } from 'playwright-core';
import { PDFDocument } from 'pdf-lib';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const AQUI = path.dirname(fileURLToPath(import.meta.url));
const SALIDA = path.join(AQUI, 'Manual_de_usuario.pdf');

const EJECUTABLE =
  process.env.CHROMIUM_PATH || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

const PIE = `
  <div style="width:100%; padding:0 16mm; font-family:Montserrat,sans-serif;
              font-size:7pt; color:#9A9A9A; display:flex;
              justify-content:space-between; align-items:center;">
    <span>Equipos Consultores · Sistema de gestión de paneles</span>
    <span class="pageNumber"></span>
  </div>`;

const nav = await chromium.launch({ executablePath: EJECUTABLE, args: ['--no-sandbox'] });

async function imprimir(archivo, opciones) {
  const p = await nav.newPage();
  await p.goto(pathToFileURL(path.join(AQUI, archivo)).href, { waitUntil: 'networkidle' });
  // Sin esto la primera página sale con la tipografía de respaldo.
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(1000);
  const pdf = await p.pdf({ format: 'A4', printBackground: true, ...opciones });
  await p.close();
  return pdf;
}

const portada = await imprimir('portada.html', {
  margin: { top: 0, bottom: 0, left: 0, right: 0 },
});

const cuerpo = await imprimir('manual.html', {
  displayHeaderFooter: true,
  headerTemplate: '<div></div>',
  footerTemplate: PIE,
  margin: { top: '18mm', bottom: '20mm', left: '16mm', right: '16mm' },
});

await nav.close();

// ── Pegar las dos partes ──
const doc = await PDFDocument.create();
for (const parte of [portada, cuerpo]) {
  const origen = await PDFDocument.load(parte);
  const paginas = await doc.copyPages(origen, origen.getPageIndices());
  paginas.forEach((pag) => doc.addPage(pag));
}
doc.setTitle('Manual de usuario — Sistema de gestión de paneles');
doc.setAuthor('Equipos Consultores');
doc.setSubject('Guía paso a paso de la aplicación de administración de paneles');
doc.setLanguage('es-UY');

await fs.writeFile(SALIDA, await doc.save());

const { size } = await fs.stat(SALIDA);
console.log(`PDF generado: ${SALIDA}`);
console.log(`${doc.getPageCount()} páginas · ${(size / 1e6).toFixed(1)} MB`);
