import {
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, ApiError } from "./client";
import type { Params } from "../../types/models";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      gcTime: 300_000,
      refetchOnWindowFocus: true,
      retry: (count, error) =>
        count < 1 &&
        error instanceof ApiError &&
        (error.status === 0 || (error.status >= 500 && error.status !== 503)),
    },
    mutations: { retry: false },
  },
});
export function useApi<T>(
  path: string,
  params?: Params,
  settings: {
    enabled?: boolean;
    poll?: boolean | ((data: T | undefined) => boolean);
    revision?: string | number;
    pollMs?: number;
    initialData?: T;
    initialDataUpdatedAt?: number;
  } = {},
) {
  return useQuery<T, ApiError>({
    queryKey: ["api", path, params ?? {}, settings.revision ?? null],
    queryFn: ({ signal }) => api.get<T>(path, params, signal),
    enabled: settings.enabled,
    initialData: settings.initialData,
    initialDataUpdatedAt: settings.initialDataUpdatedAt,
    refetchInterval: (query) =>
      (
        typeof settings.poll === "function"
          ? settings.poll(query.state.data)
          : settings.poll
      )
        ? (settings.pollMs ?? 2000)
        : false,
    refetchIntervalInBackground: false,
  });
}
export function useWrite<T, I>(action: (input: I) => Promise<T>) {
  const cache = useQueryClient();
  return useMutation<T, ApiError, I>({
    mutationFn: action,
    retry: false,
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ["api"] });
    },
    onError: async (error) => {
      if (error.status === 409)
        await cache.invalidateQueries({ queryKey: ["api"] });
    },
  });
}
export const activeInvestigation = (data: { status: string } | undefined) =>
  !!data && ["queued", "running"].includes(data.status);
export const activeProposal = (data: { status: string } | undefined) =>
  !!data && ["queued", "running", "pending_approval"].includes(data.status);
