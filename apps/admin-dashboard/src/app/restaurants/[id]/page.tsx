export default async function RestaurantDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="container py-8">
      <h1 className="text-xl font-bold tracking-tight text-ink">Restaurant {id}</h1>
      <p className="mt-1 text-sm text-ink-faint">Restaurant detail placeholder.</p>
    </div>
  );
}
