#!/usr/bin/env node
/* Alta de un usuario de la app de administración en el proyecto
 * `gestion-paneles`: crea la cuenta en Firebase Auth y su ficha en
 * Firestore (`usuarios/{uid}`) con el rol.
 *
 * El padrón es de personal de Equipos. Acá no va ningún dato de panelista:
 * la PII de panelistas vive solo en el store de bóveda (ver CLAUDE.md).
 *
 * Uso:
 *   npm install firebase-admin
 *   gcloud auth application-default login      # o GOOGLE_APPLICATION_CREDENTIALS
 *   node scripts/alta_usuario.js ana@equipos.com.uy operaciones "Ana Pérez"
 *
 * Si el usuario ya existe en Auth, no lo recrea: solo actualiza su ficha.
 * La clave inicial se genera al azar y se imprime una sola vez; conviene que
 * la persona la cambie con "¿Olvidaste tu contraseña?" en el login.
 */

const admin = require('firebase-admin');

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

const claveAlAzar = () =>
  require('crypto').randomBytes(12).toString('base64url').slice(0, 14);

(async () => {
  admin.initializeApp({ projectId: PROYECTO });
  const auth = admin.auth();
  const db = admin.firestore();

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
    actualizado_en: admin.firestore.FieldValue.serverTimestamp(),
  }, { merge: true });

  console.log(`Ficha guardada en usuarios/${usuario.uid} con rol "${rol}".`);
  if (clave) console.log(`Clave inicial (se muestra una sola vez): ${clave}`);
  process.exit(0);
})().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
