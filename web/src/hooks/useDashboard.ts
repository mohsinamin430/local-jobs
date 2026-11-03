import { useCallback, useEffect, useRef, useState } from 'react';
import type { Overview, RunDetail, Selection, Task } from '../types';
import { isActiveTask, request, runUrl } from '../lib/api';

export function useDashboard() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  const selected = useRef(selection);
  const attachedRun = useRef('');
  const lastLiveDetail = useRef(0);
  useEffect(()=>{selected.current=selection;},[selection]);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const value = await request<Overview>('/api/overview', { signal });
      if (signal?.aborted || !mounted.current) return;
      setOverview(value);
      setError('');
      if (value.active) setTask(value.active);
      setSelection(previous => previous || (value.runs[0] ? { id: value.runs[0].id, source: value.runs[0].source } : null));
    } catch (cause) {
      if (!signal?.aborted && mounted.current) setError(cause instanceof Error ? cause.message : 'Could not connect to the local service.');
    } finally { if (!signal?.aborted && mounted.current) setLoading(false); }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => { void refresh(controller.signal); }, 15_000);
    return () => { mounted.current = false; controller.abort(); window.clearInterval(timer); };
  }, [refresh]);

  useEffect(() => {
    if (!selection) { setDetail(null); return; }
    const controller = new AbortController();
    setDetailLoading(true);
    setDetail(null);
    request<RunDetail>(runUrl(selection), { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setDetail(value); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load this run.'); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selection?.id, selection?.source]);

  useEffect(() => {
    if (!task || !isActiveTask(task.state)) return;
    const controller = new AbortController();
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const value = await request<Task>(`/api/tasks/${encodeURIComponent(task.id)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (value.runId) {
          const target={id:value.runId,source:value.source || 'container'};
          const key=`${value.id}:${value.runId}`;
          if (attachedRun.current!==key) {
            attachedRun.current=key;
            setSelection(target);
            await refresh(controller.signal);
          } else if (selected.current?.id===target.id && selected.current?.source===target.source && Date.now()-lastLiveDetail.current>=5000) {
            lastLiveDetail.current=Date.now();
            const live=await request<RunDetail>(runUrl(target),{signal:controller.signal});
            if (!controller.signal.aborted && selected.current?.id===target.id && selected.current?.source===target.source) setDetail(live);
          }
        }
        if (!isActiveTask(value.state)) {
          await refresh(controller.signal);
          if (value.runId && !controller.signal.aborted) {
            const target={id:value.runId,source:value.source || 'container'};
            setSelection(target);
            const final=await request<RunDetail>(runUrl(target),{signal:controller.signal});
            if (!controller.signal.aborted) setDetail(final);
          }
        }
        if (!controller.signal.aborted) setTask(value);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not update the run status.');
      } finally { busy = false; }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 2000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [task?.id, task?.state, refresh]);

  const startRun = useCallback(async (mode: 'sample' | 'full', maxJobs: number, sources: string[]) => {
    setSubmitting(true); setError('');
    try {
      const response = await request<{ task: Task }>('/api/runs', { method: 'POST', body: JSON.stringify(mode === 'sample' ? { mode, maxJobs, sources } : { mode, sources }) });
      setTask(response.task);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not start the search.'); }
    finally { setSubmitting(false); }
  }, []);

  const cancelRun = useCallback(async () => {
    if (!task || !isActiveTask(task.state) || task.state === 'cancelling') return;
    try {
      const response = await request<Task | { task: Task }>(`/api/tasks/${encodeURIComponent(task.id)}/cancel`, { method: 'POST', body: '{}' });
      setTask('task' in response ? response.task : response);
      void refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not stop the search.'); }
  }, [task, refresh]);

  return { overview, selection, setSelection, detail, task, loading, detailLoading, submitting, error, refresh, startRun, cancelRun };
}
