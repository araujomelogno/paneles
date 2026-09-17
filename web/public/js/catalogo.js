/* R3.14 — El catálogo de atributos demográficos, del lado de la pantalla.

   Antes, cada pantalla tenía escrita su propia lista de segmentadores: la de
   consultas, la de composición y la de carga repetían «sexo, tramo etario,
   localidad» en tres archivos distintos. Desde que el vocabulario lo define
   un admin desde la app, esa lista **no puede vivir en el front**: agregar
   «nivel socioeconómico» tiene que alcanzar para que aparezca en las tres,
   sin tocar código.

   Así que lo trae el servidor y se cachea acá. El cache es por carga de
   página: se invalida solo cuando alguien edita el catálogo, que es lo que
   hace `invalidar()`. */

import * as api from './api.js';

let promesa = null;

/* El catálogo completo, una sola vez por carga de página. Las tres pantallas
   que lo necesitan lo piden al montarse y comparten la misma respuesta. */
export function cargar() {
  if (!promesa) {
    promesa = api.atributos.listar()
      .then((r) => r.items || [])
      .catch(() => {
        // Sin catálogo la pantalla tiene que seguir andando: se cae al
        // núcleo, que es lo que siempre existe. Peor sería una pantalla en
        // blanco porque falló una lista de opciones.
        promesa = null;
        return NUCLEO;
      });
  }
  return promesa;
}

export function invalidar() {
  promesa = null;
}

/* El respaldo. Son los cuatro que siembra la migración y que no se pueden
   borrar ni desactivar, así que asumirlos presentes es correcto. */
const NUCLEO = [
  { clave: 'sexo', etiqueta: 'Sexo', tipo: 'categorico', activo: true,
    es_especial: false, categorias: [] },
  { clave: 'tramo_etario', etiqueta: 'Tramo etario', tipo: 'derivado',
    activo: true, es_especial: false, categorias: [] },
  { clave: 'edad', etiqueta: 'Edad', tipo: 'derivado', activo: true,
    es_especial: false, categorias: [] },
  { clave: 'localidad', etiqueta: 'Localidad', tipo: 'categorico',
    activo: true, es_especial: false, categorias: [] },
];

export const activos = (items) => (items || []).filter((a) => a.activo);

/* Los que sirven para una cuota o una composición: los que tienen categorías
   canónicas. La edad no está —es un número continuo, y una cuota sobre eso no
   es una cuota—, pero el tramo sí. */
export const categoricos = (items) => activos(items).filter(
  (a) => a.tipo === 'categorico' || a.clave === 'tramo_etario');

/* Los que se pueden usar como criterio de filtro: todos los activos. */
export const filtrables = activos;

export const etiquetaDe = (items, clave) =>
  (items || []).find((a) => a.clave === clave)?.etiqueta || clave;

export const categoriasDe = (items, clave) =>
  ((items || []).find((a) => a.clave === clave)?.categorias || [])
    .filter((c) => c.activo !== false);

/* Cómo mostrar un valor: la etiqueta de la categoría si la tiene, y si no el
   valor tal cual. Un tramo etario se llama igual que su clave, un NSE no. */
export function etiquetaDeValor(items, clave, valor) {
  if (valor === null || valor === undefined || valor === '') return valor;
  const categoria = categoriasDe(items, clave).find((c) => c.clave === valor);
  return categoria ? categoria.etiqueta : valor;
}
