import { query } from "./db";

export async function getProduct(slug: string) {
  return query("SELECT * FROM products WHERE slug = '" + slug + "'");
}
