import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { fmtSeconds } from '../utils.js'

/** Parse & validate the range rows; returns { ranges, error }. */
function readRanges(rows) {
  const ranges = []
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i]
    const hasStart = row.start.trim() !== ''
    const hasEnd = row.end.trim() !== ''
    if (!hasStart && !hasEnd) continue // a blank row is simply ignored
    const s = Number(row.start)
    const e = Number(row.end)
    if (!hasStart || !Number.isFinite(s) || s < 0) {
      return { error: `第 ${i + 1} 行的开始时间无效（需为 ≥ 0 的秒数）` }
    }
    if (!hasEnd || !Number.isFinite(e) || e < 0) {
      return { error: `第 ${i + 1} 行的结束时间无效（需为 ≥ 0 的秒数）` }
    }
    if (e <= s) {
      return { error: `第 ${i + 1} 行的结束时间必须晚于开始时间` }
    }
    ranges.push({ start: s, end: e, note: row.note || '' })
  }
  if (ranges.length === 0) {
    return { error: '请至少填写一个时间区间' }
  }
  return { ranges }
}

export default function ExcerptsPage() {
  const { id: projectId } = useParams()
  const [project, setProject] = useState(null)
  const [rows, setRows] = useState([{ start: '', end: '', note: '' }])
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getProject(projectId).then((p) => {
      setProject(p)
    }).catch((e) => setError(e.message))
  }, [projectId])

  const patchRow = (i, patch) => {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))
    setPreview(null) // current preview no longer matches the rows
  }
  const addRow = () => {
    setRows((rs) => [...rs, { start: '', end: '', note: '' }])
    setPreview(null)
  }
  const removeRow = (i) => {
    setRows((rs) => rs.filter((_, j) => j !== i))
    setPreview(null)
  }

  const filledCount = useMemo(
    () => rows.filter((r) => r.start.trim() !== '' || r.end.trim() !== '').length,
    [rows])

  const runPreview = async () => {
    const { ranges, error: verr } = readRanges(rows)
    if (verr) {
      setPreview(null)
      setError(verr)
      return
    }
    setLoading(true); setError('')
    try {
      const data = await api.previewExcerptPack(projectId, { ranges })
      setPreview(data)
    } catch (e) {
      setPreview(null)
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const confirmDownload = async () => {
    const { ranges, error: verr } = readRanges(rows)
    if (verr) { setError(verr); return }
    setDownloading(true); setError('')
    try {
      // The server recomputes the pack for the same saved project; nothing is
      // written back, so the original project stays untouched.
      await api.excerptPackDownload(projectId, { ranges })
    } catch (e) {
      setError(e.message)
    } finally {
      setDownloading(false)
    }
  }

  if (!project && !error) return <div className="page">加载中…</div>

  const s = preview?.summary

  return (
    <div className="page excerpts-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>摘录包 · {project?.name}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
        </div>
      </div>
      {error && <div className="error">{error}</div>}

      <div className="panel ex-input">
        <h3>
          摘录区间
          <span className="muted small sp-panel-hint">
            填写多段起止时间（秒），<strong>顺序不限、允许重叠或首尾相接</strong>；
            预览时相交 / 相接的区间会自动合并。只收录<strong>完整落在合并区间内</strong>
            的转写片段，跨界片段整条列为排除项；校注按区间边界裁剪，跨越多个不相连
            区间的校注会拆成多条（保留原校注 ID 作为来源标识）。
          </span>
        </h3>
        <div className="ex-range-list">
          {rows.map((row, i) => (
            <div className="ex-range-row" key={i}>
              <span className="ex-range-idx">#{i + 1}</span>
              <input
                type="number" min="0" step="0.001" placeholder="开始（秒）"
                value={row.start}
                onChange={(e) => patchRow(i, { start: e.target.value })}
              />
              <span className="sp-arrow">–</span>
              <input
                type="number" min="0" step="0.001" placeholder="结束（秒）"
                value={row.end}
                onChange={(e) => patchRow(i, { end: e.target.value })}
              />
              <input
                className="ex-range-note" placeholder="备注（可选，会写入该区间）"
                value={row.note}
                onChange={(e) => patchRow(i, { note: e.target.value })}
              />
              <button
                className="btn icon danger" title="删除此行"
                onClick={() => removeRow(i)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
        <div className="ex-actions">
          <button className="btn" onClick={addRow}>＋ 添加区间</button>
          <button
            className="btn primary" disabled={loading || filledCount === 0}
            onClick={runPreview}
          >
            {loading ? '预览中…' : '🔍 生成预览'}
          </button>
        </div>
      </div>

      {preview && (
        <div className="panel ex-preview">
          <h3>摘录预览</h3>
          <div className="stats-grid">
            <div className="stat-box">
              <strong>输入区间</strong><span>{s.inputRanges}</span>
            </div>
            <div className="stat-box">
              <strong>合并后区间</strong><span>{s.mergedRanges}</span>
            </div>
            <div className="stat-box">
              <strong>收录片段</strong><span>{s.segments}</span>
            </div>
            <div className="stat-box">
              <strong>校注（裁剪 / 拆分后）</strong><span>{s.annotations}</span>
            </div>
            <div className={`stat-box ${s.excludedSegments ? 'has-conflict' : ''}`}>
              <strong>排除片段</strong><span>{s.excludedSegments}</span>
            </div>
          </div>

          <div className="ex-intervals">
            {preview.intervals.map((it) => (
              <div className="ex-interval" key={it.index}>
                <div className="ex-interval-head">
                  <strong>
                    区间 {it.index + 1}：{fmtSeconds(it.start)}s –{' '}
                    {fmtSeconds(it.end)}s
                    <span className="muted small">
                      （{fmtSeconds(Math.max(0, it.end - it.start))}s）
                    </span>
                  </strong>
                  <span className="badges">
                    <span className="badge">{it.segmentCount} 片段</span>
                    <span className="badge rule-tag">{it.annotationCount} 校注</span>
                    {it.excludedCount > 0 && (
                      <span className="badge danger-tag">{it.excludedCount} 排除</span>
                    )}
                  </span>
                </div>
                {it.notes.length > 0 && (
                  <div className="muted small ex-notes">
                    {it.notes.map((n, ni) => <span key={ni}>📝 {n}</span>)}
                  </div>
                )}

                {it.segments.length > 0 && (
                  <table className="run-table ex-seg-table">
                    <thead>
                      <tr>
                        <th>时间</th><th>说话人</th><th>原文</th>
                      </tr>
                    </thead>
                    <tbody>
                      {it.segments.map((seg) => (
                        <tr key={`${it.index}-${seg.id}`}>
                          <td className="ex-seg-time">
                            {fmtSeconds(seg.start)}–{fmtSeconds(seg.end)}
                          </td>
                          <td>{seg.speaker || <span className="muted">—</span>}</td>
                          <td>{seg.text}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                {it.annotations.length > 0 && (
                  <div className="ex-ann-list">
                    {it.annotations.map((a) => (
                      <div
                        className="ann-chip ex-ann-chip"
                        key={`${it.index}-${a.id}`}
                        style={{ borderLeftColor: a.color || '#5b8def' }}
                      >
                        <div className="ann-chip-head">
                          <strong>
                            {a.topic || '（未命名主题）'}
                            {a.clipped && <span className="badge danger-tag">裁剪</span>}
                            {a.partCount > 1 && (
                              <span className="badge rule-tag">
                                拆分 {a.part}/{a.partCount}
                              </span>
                            )}
                          </strong>
                        </div>
                        <div className="muted small">
                          {fmtSeconds(a.start)}s – {fmtSeconds(a.end)}s
                          {a.speaker ? ` · ${a.speaker}` : ''}
                        </div>
                        <div className="muted small ex-source">
                          来源校注：{a.sourceAnnotationId}
                          {a.sourceAnnotationId !== a.id && ` · 摘录 ID：${a.id}`}
                        </div>
                        {a.note && <div className="ann-note">{a.note}</div>}
                      </div>
                    ))}
                  </div>
                )}

                {it.excludedSegmentIds.length > 0 && (
                  <div className="ex-excluded">
                    <h4>排除项（跨界片段，不收录全文）</h4>
                    {it.excludedSegmentIds.map((sid) => {
                      const seg = preview.excludedSegments.find((x) => x.id === sid)
                      if (!seg) return null
                      return (
                        <div className="ex-excluded-row" key={`${it.index}-${sid}`}>
                          <span className="badge danger-tag">排除</span>
                          <span className="ex-seg-time">
                            {fmtSeconds(seg.start)}–{fmtSeconds(seg.end)}
                          </span>
                          <span>{seg.speaker ? `${seg.speaker}：` : ''}</span>
                          <span className="ex-excluded-text">{seg.text}</span>
                        </div>
                      )
                    })}
                  </div>
                )}

                {it.segmentCount === 0 && it.annotationCount === 0 && (
                  <div className="empty-hint">该区间内没有完整落入的片段或相交校注。</div>
                )}
              </div>
            ))}
          </div>

          <div className="sp-confirm-row">
            <button
              className="btn primary big" disabled={downloading}
              onClick={confirmDownload}
            >
              {downloading ? '下载中…' : '⬇️ 确认并下载摘录包 JSON'}
            </button>
            <span className="muted small">
              下载的 JSON 包含合并区间、收录片段、裁剪 / 拆分后的校注（含原校注 ID
              来源标识）与排除项；仅读取当前已保存的项目内容，<strong>不会修改原项目</strong>。
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
