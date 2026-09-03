"""Núcleo de la Fase 1 del sistema de gestión de paneles.

Invariante que atraviesa todo el paquete (ver CLAUDE.md): la PII vive
únicamente en el store local. Al store remoto solo viaja `id_persona`,
`ref_estudio`, el texto de respuesta ya despersonalizado y su embedding.
"""
