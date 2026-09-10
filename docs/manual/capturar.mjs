/* Captura las pantallas del manual recorriendo la app de verdad en modo demo.
 *
 *   npm install playwright-core
 *   # 1 · copiar web/public a una carpeta aparte y dejar apiKey en TU_API_KEY
 *   #     (así arranca en modo demo, con datos sembrados)
 *   # 2 · en esa copia, ocultar el banner de demo en js/app.js
 *   # 3 · servirla:  python3 -m http.server 8099
 *   node docs/manual/capturar.mjs docs/manual/capturas
 *
 * Después las capturas se pasan a JPEG (ver README.md de esta carpeta) y se
 * regenera el PDF con generar_pdf.mjs.
 */
import { chromium } from 'playwright-core';
import path from 'node:path';

const SALIDA = process.argv[2];
const URL = 'http://localhost:8099/index.html';
const errores = [];

const nav = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  // --lang manda sobre el formato de <input type="date">: sin esto la fecha
  // sale en formato de EE.UU. aunque el contexto sea es-UY.
  args: ['--no-sandbox', '--force-color-profile=srgb', '--lang=es-UY'],
});
const ctx = await nav.newContext({
  viewport: { width: 1280, height: 860 }, deviceScaleFactor: 2,
  locale: 'es-UY', timezoneId: 'America/Montevideo',
});
const p = await ctx.newPage();
p.on('pageerror', (e) => errores.push(e.message));

const tomar = async (nombre, loc) => {
  const destino = path.join(SALIDA, `${nombre}.png`);
  if (loc) await p.locator(loc).screenshot({ path: destino });
  else await p.screenshot({ path: destino });
  console.log('  ✓', nombre);
};
const esperar = (ms = 500) => p.waitForTimeout(ms);

// ── 1 · Ingreso ────────────────────────────────────────────────────
await p.goto(`${URL}?login`, { waitUntil: 'domcontentloaded' });
await p.waitForSelector('.login-wrap');
await esperar(700);
await tomar('01-ingreso');

// ── 2 · Panelistas ─────────────────────────────────────────────────
await p.goto(URL, { waitUntil: 'domcontentloaded' });
await p.waitForSelector('[data-ficha]');
await esperar(600);
await tomar('02-panelistas');

// ── 3 · Alta ───────────────────────────────────────────────────────
await p.click('#nuevo');
await p.waitForSelector('.modal-box');
await p.fill('[name="nombre"]', 'María Fernanda Suárez');
await p.fill('[name="documento"]', '4.567.890-1');
await p.fill('[name="email"]', 'mf.suarez@correo.uy');
await p.fill('[name="celular"]', '099 111 222');
await p.selectOption('[name="sexo"]', 'F');
await p.fill('[name="fecha_nacimiento"]', '1992-08-14');
await p.fill('[name="localidad"]', 'Montevideo');
await p.fill('[name="origen"]', 'dooblo');
await p.fill('[name="id_en_origen"]', 'R-101');
await esperar(300);
await tomar('03-alta-datos', '.modal-box');

await p.locator('.finalidad input[value="uso_semantico"]').check();
await p.locator('.finalidades').scrollIntoViewIfNeeded();
await esperar(300);
await tomar('04-alta-consentimiento', '.finalidades');

await p.locator('.modal-foot .btn-orange').click();
await esperar(700);
await tomar('05-alta-confirmada', '#toast-wrap');

// ── 4 · Dedup: misma cédula otra vez ───────────────────────────────
await p.click('#nuevo');
await p.waitForSelector('.modal-box');
await p.fill('[name="nombre"]', 'M. F. Suárez');
await p.fill('[name="documento"]', '4.567.890-1');
await p.locator('.modal-foot .btn-orange').click();
await esperar(700);
await tomar('06-dedup-reutiliza', '#toast-wrap');

// ── 5 · Dedup ambiguo → revisión ───────────────────────────────────
await p.click('#nuevo');
await p.waitForSelector('.modal-box');
await p.fill('[name="nombre"]', 'Ana Pérez Bentancor');
await p.fill('[name="fecha_nacimiento"]', '1988-04-12');
await p.fill('[name="localidad"]', 'Canelones');
await p.locator('.modal-foot .btn-orange').click();
await p.waitForSelector('.modal-title:has-text("revisión")');
await esperar(400);
await tomar('07-alta-en-revision', '.modal-box');

await p.locator('.modal-foot .btn-orange').click();   // Ir a revisión
await p.waitForSelector('[data-resolver]');
await esperar(500);
await tomar('08-revisiones');

await p.locator('[data-resolver]').first().click();
await p.waitForSelector('.modal-box');
await esperar(400);
await tomar('09-resolver-revision', '.modal-box');
await p.keyboard.press('Escape');
await esperar(300);

// ── 6 · Ficha ──────────────────────────────────────────────────────
await p.click('[data-pagina="panelistas"]');
await p.waitForSelector('[data-ficha]');
await p.locator('[data-ficha]').first().click();
await p.waitForSelector('.kv');
await esperar(600);
await tomar('10-ficha-panelista');

// ── 7 · Paneles ────────────────────────────────────────────────────
await p.click('[data-pagina="paneles"]');
await p.waitForSelector('[data-panel]');
await esperar(500);
await tomar('11-paneles');

await p.locator('[data-panel]').first().click();
await p.waitForSelector('#agregar');
await esperar(600);
await tomar('12-panel-miembros');

await p.click('#agregar');
await p.waitForSelector('.pick-list .pick', { timeout: 8000 });
await esperar(500);
await tomar('13-agregar-miembros', '.modal-box');
await p.keyboard.press('Escape');
await esperar(300);

// ── 8 · Encuestas ──────────────────────────────────────────────────
await p.click('[data-pagina="encuestas"]');
await p.waitForSelector('[data-encuesta]');
await esperar(500);
await tomar('14-encuestas');

await p.click('#nueva');
await p.waitForSelector('.modal-box');
await p.fill('[name="nombre"]', 'Ola 3 — Medios y consumo');
await p.fill('[name="fecha_campo"]', '2026-10-05');
await esperar(300);
await tomar('15-encuesta-nueva', '.modal-box');
await p.locator('.modal-foot .btn-orange').click();
await p.waitForSelector('#convocar');
await esperar(700);
await tomar('16-encuesta-detalle');

// ── 9 · Convocatoria ───────────────────────────────────────────────
await p.click('#convocar');
await p.waitForSelector('.modal-box');
await esperar(300);
await tomar('17-convocar-aviso', '.modal-box');
await p.locator('.modal-foot .btn-orange').click();
await esperar(900);
await tomar('18-convocatoria-resultado', '.modal-box');
await p.locator('.modal-foot .btn-outline').click();
await esperar(400);

// ── 10 · Ingesta ───────────────────────────────────────────────────
await p.click('#ingestar');
await p.waitForSelector('.modal-box');
await p.setInputFiles('#archivo', {
  name: 'ola3_respuestas.csv',
  mimeType: 'text/csv',
  buffer: Buffer.from(
    'id_en_origen,P1,P2\n' +
    'R-001,1,Porque me resulta más confiable\n' +
    'R-003,2,Lo veo todos los días\n' +
    'R-004,1,\n'
  ),
});
await esperar(800);
await p.fill('.pregunta-fila:nth-of-type(1) .p-texto', '¿Por qué medio se informa?');
await p.fill('.pregunta-fila:nth-of-type(1) .p-opciones', '1=Televisión; 2=Redes sociales');
await p.selectOption('.pregunta-fila:nth-of-type(2) .p-tipo', 'abierta');
await p.fill('.pregunta-fila:nth-of-type(2) .p-texto', '¿Por qué lo elige?');
await esperar(400);
await tomar('19-ingesta', '.modal-box');

await p.locator('.modal-foot .btn-orange').click();
await esperar(1200);
await tomar('20-ingesta-resultado', '.modal-box');
await p.locator('.modal-foot .btn-outline').click();
await esperar(600);

// ── 11 · Cruce entre stores ────────────────────────────────────────
await p.click('#verificar');
await p.waitForSelector('#cruce .alert');
await esperar(500);
await tomar('21-cruce', '.card:has(#cruce)');

// ── 12 · Cumplimiento ──────────────────────────────────────────────
await p.click('[data-pagina="cumplimiento"]');
await p.waitForSelector('#auditoria .alert', { timeout: 8000 });
await esperar(600);
await tomar('22-cumplimiento');

// ── 13 · Baja total ────────────────────────────────────────────────
await p.click('[data-pagina="panelistas"]');
await p.waitForSelector('[data-ficha]');
await p.locator('[data-ficha]').first().click();
await p.waitForSelector('[data-retiro="todas"]');
await p.click('[data-retiro="todas"]');
await p.waitForSelector('.modal-box');
await esperar(400);
await tomar('23-baja-total', '.modal-box');

// ── 14 · Consultas ─────────────────────────────────────────────────
await p.keyboard.press('Escape');            // cerrar la confirmación de baja
await esperar(300);
await p.click('[data-pagina="consultas"]');
await p.waitForSelector('#agregar-semantico');
await esperar(500);

await p.click('#agregar-semantico');
await p.waitForSelector('.modal-box');
await p.fill('textarea[name="texto"]', 'gente a la que le encanta el fernet');
await esperar(300);
await tomar('24-consulta-criterio', '.modal-box');
await p.locator('.modal-foot .btn-orange').click();
await esperar(400);

await p.click('#correr');
await p.waitForSelector('#resultado table', { timeout: 20000 });
await esperar(1000);
await tomar('25-consulta-resultado');
await tomar('26-consulta-ranking', '.card:has(#ver-nombres)');
await tomar('27-consulta-excluidos', '.card:has(th:text-is("Motivo"))');
await tomar('28-consulta-diagnostico', '.card:has(th:text-is("Etapa"))');

// La evidencia de una persona del ranking, con su procedencia.
await p.locator('#resultado [data-detalle]').first().click();
await p.waitForSelector('.modal-box');
await esperar(500);
await tomar('29-consulta-evidencia', '.modal-box');
await p.keyboard.press('Escape');
await esperar(300);

// Reidentificación: el aviso antes de traducir los identificadores.
await p.click('#ver-nombres');
await p.waitForSelector('.modal-box');
await esperar(400);
await tomar('30-consulta-reidentificar', '.modal-box');
await p.locator('.modal-foot .btn-dark').click();
await esperar(900);
await tomar('31-consulta-con-nombres', '.card:has(#ver-nombres)');

// Parámetros del oleoducto.
await p.click('#parametros');
await p.waitForSelector('.modal-box');
await esperar(400);
await tomar('32-consulta-parametros', '.modal-box');
await p.keyboard.press('Escape');
await esperar(300);

// Consulta mixta: se le suma un criterio demográfico y el modo laxo.
await p.click('#agregar-demografico');
await p.waitForSelector('.modal-box');
await p.selectOption('[name="dimension"]', 'localidad');
await p.fill('[name="valor"]', 'Montevideo');
await esperar(300);
await tomar('33-criterio-demografico', '.modal-box');
await p.locator('.modal-foot .btn-orange').click();
await esperar(400);
await p.selectOption('#modo', 'laxo');
await esperar(300);
await tomar('34-consulta-criterios', '.card:has(#criterios)');
await p.click('#correr');
await p.waitForSelector('#resultado table', { timeout: 20000 });
await esperar(1000);
await tomar('35-consulta-puente', '.card:has(dt:text-is("Puente entre stores"))');

// Guardar la consulta para reusarla.
await p.click('#guardar');
await p.waitForSelector('.modal-box');
await p.fill('[name="nombre"]', 'Fernet en Montevideo');
await p.fill('[name="descripcion"]', 'Para la próxima ola de bebidas.');
await esperar(300);
await tomar('36-consulta-guardar', '.modal-box');
await p.keyboard.press('Escape');
await esperar(300);

// ── 15 · Composición ───────────────────────────────────────────────
await p.click('[data-pagina="composicion"]');
await p.waitForSelector('#cargar-objetivo');
await esperar(700);
await tomar('37-composicion');

await p.click('#cargar-objetivo');
await p.waitForSelector('.modal-box');
await p.selectOption('#obj-dimension', 'tramo_etario');
await esperar(400);
await tomar('38-composicion-objetivo', '.modal-box');
await p.keyboard.press('Escape');
await esperar(300);

await p.selectOption('#cruce', 'sexo,tramo_etario');
await p.waitForSelector('.badge-off:text-is("descriptivo")', { timeout: 8000 });
await esperar(700);
await tomar('39-composicion-cruce', '.card:has(.badge-off:text-is("descriptivo"))');

// ── 16 · Participación ─────────────────────────────────────────────
await p.click('[data-pagina="participacion"]');
await p.waitForSelector('.stat-grid');
await esperar(800);
await tomar('40-participacion');

// ── 17 · Configuración ─────────────────────────────────────────────
await p.click('[data-pagina="configuracion"]');
await p.waitForSelector('[data-rol]');
await esperar(700);
await tomar('41-configuracion');

await p.click('#nuevo');
await p.waitForSelector('.modal-box');
await p.fill('[name="email"]', 'lucia.torres@equipos.com.uy');
await p.fill('[name="nombre"]', 'Lucía Torres');
await p.selectOption('[name="rol"]', 'analista');
await esperar(400);
await tomar('42-usuario-alta', '.modal-box');
await p.locator('.modal-foot .btn-orange').click();
await p.waitForSelector('.modal-title:has-text("Usuario creado")', { timeout: 8000 });
await esperar(500);
await tomar('43-usuario-acceso', '.modal-box');
await p.keyboard.press('Escape');
await esperar(600);
await tomar('44-usuarios-auditoria', '.card:has(th:text-is("Acción"))');

console.log(errores.length ? '\nERRORES:\n' + errores.join('\n') : '\nsin errores de consola');
await nav.close();
