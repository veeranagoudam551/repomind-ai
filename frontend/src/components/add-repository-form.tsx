"use client";

import { useActionState, useEffect, useRef } from "react";
import { Plus } from "lucide-react";
import { addRepository, type RepositoryFormState } from "@/app/actions/repositories";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function AddRepositoryForm() {
  const [state, action, pending] = useActionState<RepositoryFormState, FormData>(
    addRepository,
    undefined
  );
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (state?.successAt) {
      formRef.current?.reset();
    }
  }, [state?.successAt]);

  return (
    <form ref={formRef} action={action} className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row">
        <Input
          name="github_url"
          placeholder="owner/repo or https://github.com/owner/repo"
          required
          className="flex-1"
        />
        <Button type="submit" disabled={pending}>
          <Plus />
          {pending ? "Adding…" : "Add repository"}
        </Button>
      </div>
      {state?.error && <p className="text-sm text-destructive">{state.error}</p>}
    </form>
  );
}
