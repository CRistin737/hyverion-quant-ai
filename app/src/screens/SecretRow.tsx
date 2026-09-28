import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, LockKeyhole } from "lucide-react";
import { useState } from "react";

import { deleteSecret, keychainAvailable, saveSecret, secretStatus, type SecretName } from "@/api/secrets";
import { Button } from "@/ui/button";
import { Badge } from "@/ui/feedback";
import { Field, Input } from "@/ui/form";
import { Dialog } from "@/ui/overlay";
import { useToast } from "@/ui/toast";

export function useSecretStatus(names: SecretName[]) {
  return useQuery({ queryKey: ["secrets", ...names], queryFn: () => secretStatus(names), staleTime: 60_000 });
}

/**
 * One keychain-backed secret. The value goes webview → Tauri → macOS Keychain
 * and is never shown again, never sent over HTTP and never stored in config.
 */
export function SecretRow({ name, label, description, compact = false }: { name: SecretName; label: string; description?: string; compact?: boolean }) {
  const status = useSecretStatus([name]);
  const stored = Boolean(status.data?.[name]);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const queryClient = useQueryClient();
  const toast = useToast();
  const available = keychainAvailable();

  const save = useMutation({
    mutationFn: () => saveSecret(name, value),
    onSuccess: async () => {
      setValue("");
      setOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["secrets"] });
      toast({ tone: "success", title: "Guardado en el Llavero", description: label });
    },
  });
  const remove = useMutation({
    mutationFn: () => deleteSecret(name),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["secrets"] });
      toast({ tone: "info", title: "Eliminado del Llavero", description: label });
    },
  });

  return (
    <div className={compact ? "flex items-center justify-end gap-3 py-1.5" : "flex items-center gap-4 border-b border-line py-3 last:border-0"}>
      {compact ? null : <KeyRound size={16} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-body text-fg">{label}</span>
        {description ? <span className="text-caption text-fg-muted">{description}</span> : null}
      </div>
      {!available ? (
        <Badge>Solo en la app de escritorio</Badge>
      ) : stored ? (
        <>
          <Badge tone="positive">{compact ? "Guardada" : "Guardado en el Llavero"}</Badge>
          <Button size="sm" onClick={() => setOpen(true)}>
            Reemplazar
          </Button>
          <Button size="sm" variant="ghost" loading={remove.isPending} onClick={() => remove.mutate()}>
            Eliminar
          </Button>
        </>
      ) : (
        <Button size="sm" icon={LockKeyhole} onClick={() => setOpen(true)}>
          {compact ? "Guardar" : "Guardar en el Llavero"}
        </Button>
      )}
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setValue("");
        }}
        title={stored ? `Reemplazar ${label}` : `Guardar ${label}`}
        description="Se guarda cifrado en el Llavero de macOS. No se volverá a mostrar ni se envía por la red."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancelar
            </Button>
            <Button variant="primary" disabled={!value.trim()} loading={save.isPending} onClick={() => save.mutate()}>
              Guardar
            </Button>
          </>
        }
      >
        <Field label={label} error={save.error ? save.error.message : null}>
          {(id, describedBy) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              type="password"
              autoComplete="off"
              spellCheck={false}
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && value.trim()) save.mutate();
              }}
            />
          )}
        </Field>
      </Dialog>
    </div>
  );
}
