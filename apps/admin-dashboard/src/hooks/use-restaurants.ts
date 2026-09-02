import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/hooks/query-keys";
import type { PaginatedResponse, Restaurant, RestaurantSummary } from "@/lib/api/types";

export function useRestaurants(page: number, pageSize = 20) {
  return useQuery({
    queryKey: queryKeys.restaurants.list(page),
    queryFn: () =>
      apiGet<PaginatedResponse<RestaurantSummary>>("admin/restaurants", {
        page,
        page_size: pageSize,
      }),
  });
}

export function useRestaurant(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.restaurants.detail(id ?? ""),
    queryFn: () => apiGet<Restaurant>(`admin/restaurants/${id}`),
    enabled: id !== undefined,
  });
}
