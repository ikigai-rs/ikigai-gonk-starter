/**
 * JSON, as Deno's standard library already speaks it: `JSON.parse` and `JSON.stringify` are
 * built in, so this module adds no parser and no printer — only the type the rules answer in.
 *
 * Every answer of the rules is a `Json` value: the same shape the HTTP API serves and the
 * scenario vectors (`openspec/vectors.json`) expect. Nothing here is a dependency.
 */

export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json };

/** A JSON object, as opposed to an array or a value that is not an object at all. */
export function isObject(value: unknown): value is { [key: string]: Json } {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
