"use client";

import { use } from "react";
import { useRestaurant } from "@/hooks/use-restaurants";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/error-state";

export default function RestaurantDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: restaurant, isPending, isError, error, refetch } = useRestaurant(id);

  if (isPending) {
    return (
      <div className="container flex flex-col gap-3 py-8">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="container py-8">
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  const dishCount = restaurant.menus.reduce(
    (total, menu) =>
      total + menu.categories.reduce((sum, category) => sum + category.dishes.length, 0),
    0,
  );

  return (
    <div className="container py-8">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold tracking-tight text-ink">{restaurant.name}</h1>
        <Badge variant={restaurant.is_active ? "ok" : "outline"}>
          {restaurant.is_active ? "Active" : "Inactive"}
        </Badge>
      </div>
      {restaurant.description && (
        <p className="mt-1 text-sm text-ink-faint">{restaurant.description}</p>
      )}

      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-[12.5px] text-ink-faint">Locations</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-ink">
            {restaurant.locations.length}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-[12.5px] text-ink-faint">Menus</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-ink">
            {restaurant.menus.length}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-[12.5px] text-ink-faint">Dishes</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-ink">{dishCount}</CardContent>
        </Card>
      </div>

      {restaurant.locations.length > 0 && (
        <div className="mt-6">
          <h2 className="text-[13px] font-semibold text-ink">Locations</h2>
          <div className="mt-2 flex flex-col gap-2">
            {restaurant.locations.map((location) => (
              <Card key={location.id}>
                <CardContent className="pt-4 text-[12.5px] text-ink-soft">
                  {location.label && <div className="font-medium text-ink">{location.label}</div>}
                  <div>
                    {location.address_line1}
                    {location.address_line2 ? `, ${location.address_line2}` : ""}
                  </div>
                  <div>
                    {location.city}
                    {location.state ? `, ${location.state}` : ""} {location.postal_code ?? ""}{" "}
                    {location.country}
                  </div>
                  {location.phone && <div>{location.phone}</div>}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {restaurant.menus.map((menu) => (
        <div key={menu.id} className="mt-6">
          <h2 className="text-[13px] font-semibold text-ink">{menu.name}</h2>
          {menu.categories.map((category) => (
            <div key={category.id} className="mt-3">
              <h3 className="text-[12.5px] font-medium text-ink-soft">{category.name}</h3>
              <div className="mt-2 flex flex-col gap-2">
                {category.dishes.map((dish) => (
                  <Card key={dish.id}>
                    <CardContent className="flex items-center justify-between pt-4">
                      <div>
                        <div className="text-[12.5px] font-medium text-ink">{dish.name}</div>
                        {dish.description && (
                          <div className="text-[11.5px] text-ink-faint">{dish.description}</div>
                        )}
                      </div>
                      <div className="text-[12.5px] text-ink-soft">
                        {dish.price ? `${dish.currency} ${dish.price}` : "—"}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
