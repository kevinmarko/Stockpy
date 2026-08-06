import { useState } from "react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useMutation } from "../../hooks/useMutation";
import { Loading, EmptyState, ErrorState, Notice, Button, Table } from "../ui";

export function TradeApprovalCenter() {
  const { data: trades, loading, error, status, reload } = useApi(() => api.getClsPendingApprovals(), []);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const approve = useMutation((lotIds: number[]) => api.approveClsBulk(lotIds));

  if (loading) return <Loading />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;

  const list = trades ?? [];

  const handleToggle = (id: number) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const handleToggleAll = () => {
    if (selected.size === list.length) setSelected(new Set());
    else setSelected(new Set(list.map((t) => t.lot_id)));
  };

  const handleApprove = async () => {
    if (selected.size === 0) return;
    const result = await approve.run(Array.from(selected));
    if (result) {
      setSelected(new Set());
      reload();
    }
  };

  if (list.length === 0) {
    return <EmptyState title="No pending trades" hint="TLH opportunities the background scanner flags will show up here for approval." />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {approve.error && <Notice variant="warn">{approve.error}</Notice>}
      {approve.result && <Notice variant="success">Approved {approve.result.count} trade{approve.result.count === 1 ? "" : "s"}.</Notice>}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>Pending Actions</h3>
        <Button variant="primary" disabled={selected.size === 0} pending={approve.pending} onClick={handleApprove}>
          Approve Selected ({selected.size})
        </Button>
      </div>

      <div style={{ overflowX: "auto" }}>
        <Table>
          <thead>
            <tr>
              <th style={{ width: 40 }}>
                <input
                  type="checkbox"
                  checked={list.length > 0 && selected.size === list.length}
                  onChange={handleToggleAll}
                  aria-label="Select all pending trades"
                />
              </th>
              <th>Lot ID</th>
              <th>Position ID</th>
              <th className="num">Cost Basis</th>
              <th className="num">Unrealized Loss</th>
            </tr>
          </thead>
          <tbody>
            {list.map((trade) => (
              <tr key={trade.lot_id} style={{ borderBottom: `1px solid ${theme.border}` }}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(trade.lot_id)}
                    onChange={() => handleToggle(trade.lot_id)}
                    aria-label={`Select lot ${trade.lot_id}`}
                  />
                </td>
                <td>{trade.lot_id}</td>
                <td>{trade.position_id}</td>
                <td className="num">${trade.cost_basis.toFixed(2)}</td>
                <td className="num" style={{ color: theme.decline }}>
                  {trade.unrealized_loss_pct != null ? `${(trade.unrealized_loss_pct * 100).toFixed(1)}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </div>
  );
}
