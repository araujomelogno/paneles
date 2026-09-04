"""Núcleo de la Fase 1 del sistema de gestión de paneles.

Invariante que atraviesa todo el paquete (ver CLAUDE.md): la PII vive
únicamente en el store de bóveda. Al store semántico solo viaja `id_persona`,
`ref_estudio`, el texto de respuesta ya despersonalizado y su embedding.
"""
