#!/usr/bin/env node
/* Alta de un usuario de la app de administración en el proyecto
 * `gestion-paneles`: crea la cuenta en Firebase Auth y su ficha en
 * Firestore (`usuarios/{uid}`) con el rol.
 *
 * El padrón es de personal de Equipos. Acá no va ningún dato de panelista:
 * la PII de panelistas vive solo en la bóveda (ver CLAUDE.md).
 *
 * Uso:
 *   npm install firebase-admin
 *   gcloud auth application-default login      # ADC
 *   node scripts/alta_usuario.js ana@equipos.com.uy operaciones "Ana Pérez"
 *
 * Si el usuario ya existe en Auth, no lo recrea: solo actualiza su ficha.
 * La clave inicial se genera al azar y se imprime una sola vez; conviene que
 * la persona la cambie con "¿Olvidaste tu contraseña?" en el login.
 *
 * Sobre los imports: se usa la API modular por subpaths
 * (`firebase-admin/app`, `/auth`, `/firestore`). Desde la v13 el export raíz
 * del paquete dejó de exponer `admin.auth` y `admin.firestore`, así que la
 * forma vieja `require('firebase-admin').auth()` falla con
 * "admin.auth is not a function". Los subpaths existen desde la v10 y andan
 * en todas las versiones desde entonces.
 */

const { randomBytes } = require('node:crypto');
const { initializeApp } = require('firebase-admin/app');
const { getAuth } = require('firebase-admin/auth');
const { getFirestore, FieldValue } = require('firebase-admin/firestore');

const PROYECTO = process.env.FIREBASE_PROJECT || 'gestion-paneles';
const ROLES = ['admin', 'operaciones', 'analista', 'dpo'];

const [email, rol, nombre] = process.argv.slice(2);

if (!email || !rol) {
  console.error('Uso: node scripts/alta_usuario.js <email> <rol> ["Nombre Apellido"]');
  console.error(`Roles: ${ROLES.join(', ')}`);
  process.exit(1);
}
if (!ROLES.includes(rol)) {
  console.error(`Rol desconocido: ${rol}. Roles válidos: ${ROLES.join(', ')}`);
  process.exit(1);
}

const claveAlAzar = () => randomBytes(12).toString('base64url').slice(0, 14);

/* Los errores de credenciales son los más frecuentes acá y el mensaje crudo
   del SDK no dice qué hacer. */
function explicar(error) {
  const texto = String(error?.message || error);
  if (/could not (load|find) the default credentials|application default/i.test(texto)) {
    return `${texto}\n\nFaltan las credenciales por defecto. Corré:\n` +
           '  gcloud auth application-default login';
  }
  if (/PERMISSION_DENIED|permission/i.test(texto)) {
    return `${texto}\n\nRevisá que tu cuenta tenga permisos sobre el proyecto ` +
           `«${PROYECTO}» y que sea el proyecto correcto ` +
           '(FIREBASE_PROJECT lo sobrescribe).';
  }
  if (/NOT_FOUND|database.*does not exist/i.test(texto)) {
    return `${texto}\n\n¿Creaste la base de Firestore? Consola Firebase → ` +
           'Firestore Database → Crear base de datos.';
  }
  return texto;
}

(async () => {
  initializeApp({ projectId: PROYECTO });
  const auth = getAuth();
  const db = getFirestore();

  let usuario;
  let clave = null;
  try {
    usuario = await auth.getUserByEmail(email);
    console.log(`Ya existía en Auth: ${usuario.uid}`);
  } catch (error) {
    if (error.code !== 'auth/user-not-found') throw error;
    clave = claveAlAzar();
    usuario = await auth.createUser({
      email, password: clave, displayName: nombre || email, emailVerified: false,
    });
    console.log(`Creado en Auth: ${usuario.uid}`);
  }

  await db.collection('usuarios').doc(usuario.uid).set({
    nombre: nombre || usuario.displayName || email,
    email,
    rol,
    activo: true,
    actualizado_en: FieldValue.serverTimestamp(),
  }, { merge: true });

  console.log(`Ficha guardada en usuarios/${usuario.uid} con rol "${rol}".`);
  if (clave) console.log(`Clave inicial (se muestra una sola vez): ${clave}`);
  process.exit(0);
})().catch((error) => {
  console.error(explicar(error));
  process.exit(1);
});
