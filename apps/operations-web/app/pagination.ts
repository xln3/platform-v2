import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export type CursorPage<T> = {
  data: T[];
  nextCursor: string | null;
  hasMore: boolean;
  totalCount?: number;
  counts?: Record<string, number>;
};

export type NumberedPage<T> = {
  data: T[];
  page: number;
  pageSize: number;
  totalCount: number;
  totalPages: number;
};

export type CursorCollectionState = 'loading' | 'ready' | 'failed';

export function pageFromResponse<T>(data: T[], response: Response): CursorPage<T> {
  const nextCursor = response.headers.get('X-Next-Cursor');
  const hasMore = response.headers.get('X-Has-More') === 'true';
  const totalCount = readCount(response, 'X-Total-Count');
  return {
    data,
    nextCursor,
    hasMore,
    ...(totalCount === undefined ? {} : { totalCount }),
  };
}

export function numberedPageFromResponse<T>(data: T[], response: Response): NumberedPage<T> {
  const page = requiredCount(response, 'X-Page');
  const pageSize = requiredCount(response, 'X-Page-Size');
  const totalCount = requiredCount(response, 'X-Total-Count');
  const totalPages = requiredCount(response, 'X-Page-Count');
  return { data, page, pageSize, totalCount, totalPages };
}

export function readCount(response: Response, header: string): number | undefined {
  const raw = response.headers.get(header);
  if (raw === null || !/^\d+$/.test(raw)) return undefined;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) ? parsed : undefined;
}

function requiredCount(response: Response, header: string): number {
  const value = readCount(response, header);
  if (value === undefined) throw new Error(`missing_or_invalid_${header.toLowerCase()}`);
  return value;
}

export function useNumberedCollection<T>(
  loader: (page: number) => Promise<NumberedPage<T>>,
  resetKey: string,
) {
  const [requestedPage, setRequestedPage] = useState(1);
  const [page, setPage] = useState<NumberedPage<T> | null>(null);
  const [state, setState] = useState<CursorCollectionState>('loading');
  const requestSerial = useRef(0);
  const initializedResetKey = useRef<string | null>(null);

  const refresh = useCallback(
    async (background = false) => {
      const requestId = ++requestSerial.current;
      if (!background) setState('loading');
      try {
        const next = await loader(requestedPage);
        if (requestId !== requestSerial.current) return;
        setPage(next);
        setRequestedPage(next.page);
        setState('ready');
      } catch {
        if (requestId !== requestSerial.current) return;
        if (!background) setState('failed');
      }
    },
    [loader, requestedPage],
  );

  useEffect(() => {
    if (initializedResetKey.current === null) {
      initializedResetKey.current = resetKey;
      return;
    }
    initializedResetKey.current = resetKey;
    setRequestedPage(1);
    setPage(null);
    setState('loading');
    requestSerial.current += 1;
  }, [resetKey]);

  useEffect(() => {
    void refresh();
    return () => {
      requestSerial.current += 1;
    };
  }, [refresh, resetKey]);

  const goToPage = useCallback((nextPage: number) => {
    if (!Number.isSafeInteger(nextPage)) return;
    setRequestedPage(Math.max(1, nextPage));
  }, []);

  return {
    data: page?.data ?? [],
    meta: page,
    state,
    pageNumber: page?.page ?? requestedPage,
    goToPage,
    refresh,
  };
}

export function useCursorCollection<T>(
  loader: (cursor?: string) => Promise<CursorPage<T>>,
  resetKey: string,
) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [backStack, setBackStack] = useState<Array<string | null>>([]);
  const [page, setPage] = useState<CursorPage<T> | null>(null);
  const [state, setState] = useState<CursorCollectionState>('loading');
  const requestSerial = useRef(0);
  const initializedResetKey = useRef<string | null>(null);

  const refresh = useCallback(
    async (background = false) => {
      const requestId = ++requestSerial.current;
      if (!background) setState('loading');
      try {
        const next = await loader(cursor ?? undefined);
        if (requestId !== requestSerial.current) return;
        if (next.data.length === 0 && cursor !== null && backStack.length > 0) {
          setBackStack((current) => current.slice(0, -1));
          setCursor(backStack.at(-1) ?? null);
          return;
        }
        setPage(next);
        setState('ready');
      } catch {
        if (requestId !== requestSerial.current) return;
        if (!background) setState('failed');
      }
    },
    [backStack, cursor, loader],
  );

  useEffect(() => {
    if (initializedResetKey.current === null) {
      initializedResetKey.current = resetKey;
      return;
    }
    initializedResetKey.current = resetKey;
    setCursor(null);
    setBackStack([]);
    setPage(null);
    setState('loading');
    requestSerial.current += 1;
  }, [resetKey]);

  useEffect(() => {
    void refresh();
    return () => {
      requestSerial.current += 1;
    };
  }, [refresh, resetKey]);

  const next = useCallback(() => {
    if (!page?.hasMore || !page.nextCursor) return;
    setBackStack((current) => [...current, cursor]);
    setCursor(page.nextCursor);
  }, [cursor, page]);

  const previous = useCallback(() => {
    if (backStack.length === 0) return;
    setCursor(backStack.at(-1) ?? null);
    setBackStack((current) => current.slice(0, -1));
  }, [backStack]);

  return {
    data: page?.data ?? [],
    meta: page,
    state,
    pageNumber: backStack.length + 1,
    hasPrevious: backStack.length > 0,
    hasNext: Boolean(page?.hasMore && page.nextCursor),
    next,
    previous,
    refresh,
  };
}

export function usePageWindow<T>(items: readonly T[], resetKey: string, pageSize: number) {
  if (!Number.isSafeInteger(pageSize) || pageSize < 1) {
    throw new Error('invalid_page_window_size');
  }
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));

  useEffect(() => setPage(1), [resetKey]);
  useEffect(() => setPage((current) => Math.min(current, pageCount)), [pageCount]);

  const visibleItems = useMemo(
    () => items.slice((page - 1) * pageSize, page * pageSize),
    [items, page, pageSize],
  );
  return { page, pageCount, setPage, visibleItems };
}
