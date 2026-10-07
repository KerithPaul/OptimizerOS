import { getProduct } from "../../../lib/product-service";

export async function generateMetadata({
  params,
}: {
  params: { slug: string };
}) {
  const product = await getProduct(params.slug);
  return { title: "Product" };
}

export default async function ProductPage({
  params,
}: {
  params: { slug: string };
}) {
  const product = await getProduct(params.slug);
  return (
    <main>
      <h1>Product {params.slug}</h1>
    </main>
  );
}
