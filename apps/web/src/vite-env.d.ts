/// <reference types="vite/client" />

/* echarts-gl ships no types.
 *
 * It is imported dynamically and only for the 3D surface, and the import is
 * wrapped in a catch: it is built against a different echarts major than the
 * one pinned here, so it is treated as a renderer that may or may not be
 * available rather than as a dependency the build can rely on. A declaration
 * rather than an `any` cast at the call site, so the shape of that decision is
 * visible in one place. */
declare module 'echarts-gl'
