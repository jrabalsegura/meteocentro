# Fase 5 Históricos y resúmenes

## Objetivo

Permitir explorar la evolución de cada estación y consultar días, meses y años sin alterar el significado de los datos. Es una fase central del producto, al mismo nivel que el mapa.

Dependencias: ingestión y contratos de las redes habilitadas, más la ficha de fase 4. La falta de archivo anterior en una red no impide representar las observaciones guardadas desde su incorporación.

## Funciones

- Gráficos por estación con 24 h, 48 h, 72 h, 7 días, 14 días y fechas personalizadas.
- Series de temperatura, humedad, presión, viento, racha y precipitación según sensores y productos disponibles.
- Tabla de registros con fecha/hora, unidad, fuente y calidad; alternancia entre tabla y gráfico.
- Vista de datos diarios con mínimos, máximos, medias justificadas y acumulados comparables.
- Resúmenes mensuales y anuales y efemérides del periodo disponible, con fechas de cobertura.
- Exportación CSV limitada y disponible solo para los datos cuyo permiso lo admita.

## Trabajo

1. Implementar `/api/v1/stations/{id}/series`, `/daily`, `/records` y `/export.csv`, y una consulta de resúmenes de la red por día. Validar `from`, `to`, métrica, origen y resolución. Definir límites de intervalo y puntos, paginación de tablas y consultas largas mediante agregados.
2. Guardar por separado los resúmenes publicados por la fuente y los calculados por Meteocentro. Exponer `aggregation_method`, ventana, cobertura, procedencia y estado provisional. No sustituir silenciosamente unos por otros.
3. Calcular agregados horarios y diarios de forma incremental, con versión del cálculo y regeneración de ventanas afectadas por correcciones. Guardar mínimos y máximos además de medias; un gráfico reducido no debe esconder un pico real.
4. Representar huecos mediante cortes. No interpolar puntos por defecto ni convertir días sin datos en ceros. Informar primera fecha, última fecha y disponibilidad por métrica y resolución.
5. Importar histórico remoto en trabajos reanudables por fuente, estación y ventana. Empezar por el piloto de 30 días, con presupuesto propio y prioridad inferior a datos actuales. Impedir solapamientos duplicados con la ingestión continua.
6. Mostrar cambios de emplazamiento, fuente y método estadístico en la serie. Si dos fuentes están vinculadas, mantener selección por origen; no construir una serie histórica continua mezclándolas sin aviso.
7. Añadir tablas y gráficos de interfaz y completar el mapa de extremos diarios usando resúmenes de periodo comparable.
8. Medir tamaño de registros, índices y rendimiento. Implementar particionado mensual de observaciones si se mantiene la previsión de archivo indicada; crear particiones futuras y verificar que sus restricciones preservan la unicidad con la clave temporal.
9. Preparar retención configurable con simulación de lo que se borraría, sin activar purga destructiva inicialmente. Los agregados se verifican antes de retirar detalle y se conserva el permiso de cada fuente.

## Reglas meteorológicas que hay que implementar

**Periodos.** Los días civiles se calculan en `Europe/Madrid`, con límites convertidos a UTC; no se asume que siempre duren 24 horas. Los resúmenes externos con día UTC o climatológico conservan su ventana original. No se reconstruye un día civil distinto a partir de un único total externo.

**Lluvia.** No sumar sucesivos contadores diarios. Para un contador se calculan diferencias válidas dentro de su mismo periodo, distinguiendo reinicio documentado, descenso inesperado y hueco. Por ejemplo, lecturas 2,0; 2,4; 2,4; 3,1 mm acumulan 1,1 mm observados entre la primera y la última, no 9,9 mm; el total desde el inicio del día solo puede afirmarse si se conoce la base del contador. Los incrementos horarios se suman sin solaparlos; los acumulados móviles no se suman entre sí. La intensidad publicada y la calculada a partir de intervalos se etiquetan de manera distinta.

**Medias y viento.** Las medias calculadas de muestras irregulares deben ponderar el tiempo de cobertura con una regla documentada y no extender el último valor a través de grandes huecos. Los métodos del proveedor se preservan. La dirección del viento requiere media circular/vectorial y tratamiento de calma, no media aritmética de grados. Una racha es un máximo del periodo indicado.

**Cobertura.** Calcular cobertura por métrica a partir de los intervalos cubiertos y la cadencia conocida. Un total parcial se marca como tal. Establecer un umbral de elegibilidad configurable para comparar días completos; el día en curso se etiqueta provisional y se compara hasta un corte temporal coherente.

**Calidad.** Conservar valores y banderas de origen. Los inválidos quedan fuera de cálculos públicos por defecto, con política consistente y versión de reglas. Los sospechosos plausibles no se borran automáticamente. La exclusión de una estación filtra todas las salidas, incluido el CSV, aunque el agregado ya existiese.

## Comprobación de salida

- [ ] Probar un día de 23 horas y uno de 25 horas, incluido el intervalo repetido de otoño.
- [ ] Probar cero real, hueco, contador que se reinicia, corrección hacia abajo y lluvia horaria solapada.
- [ ] Una serie sin archivo anterior muestra “sin datos disponibles” y su fecha inicial.
- [ ] La importación antigua no sustituye la última lectura actual ni duplica el histórico.
- [ ] Una corrección de origen recalcula los periodos afectados.
- [ ] Los extremos del archivo indican intervalo disponible; no se presentan como récord absoluto universal.
- [ ] Un CSV respeta exclusiones, límites y autorización; campos de texto no ejecutan fórmulas al abrirlo en una hoja de cálculo.
- [ ] Las consultas de un año se prueban con volumen representativo y se documentan latencia y plan de consulta.

## Prompt para Codex

```text
Lee AGENTS.md, resumen, contratos y estado. Implementa solo
docs/fases/FASE_05_HISTORICOS.md. Construye series, diarios,
resúmenes, efemérides e importación reanudable con cobertura visible.
Dedica las pruebas a lluvia, horarios, nulos, correcciones e idempotencia.
No inventes curvas antiguas a partir de resúmenes diarios ni mezcles
periodos incompatibles. Prueba las consultas con PostgreSQL y verifica
gráficos en móvil y escritorio. Actualiza docs/ESTADO.md.
```
