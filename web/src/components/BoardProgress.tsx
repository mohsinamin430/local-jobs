import type { BoardProgress as Progress } from '../types';
import { Badge } from './Badge';

export function BoardProgress({ progress, now }: { progress?:Record<string,Progress>; now:number }) {
  if (!progress || !Object.keys(progress).length) return null;
  return <div className="board-progress" aria-label="Live job-board progress">{Object.entries(progress).map(([id,board])=>{
    const remaining=Math.max(0,Math.ceil(board.retryAtEpoch-now/1000));
    return <div className="board-progress-row" key={id}><strong>{board.name}</strong><Badge value={board.state}/>
      {board.state==='cooling_down' && <span>Retry in {Math.floor(remaining/60)}m {remaining%60}s{board.waitingOn && board.waitingOn!==id ? ` · waiting for ${board.waitingOn}` : ''}</span>}
      {board.retryCount>0 && board.state!=='cooling_down' && <span>Resumed after cooldown</span>}
    </div>;
  })}<p>One combined result set. Each board resumes automatically when its cooldown ends.</p></div>;
}
