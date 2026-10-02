<script setup lang="ts">
import type { CheckStatus } from '~/composables/useConnectionChecks'

const { apiBase, rows, loading, error, runAll, runCheck } = useConnectionChecks()

const severity: Record<CheckStatus, 'success' | 'danger' | 'secondary'> = {
  ok: 'success',
  error: 'danger',
  pending: 'secondary',
}

// Checks call the API from the browser, so run them only on the client.
onMounted(runAll)
</script>

<template>
  <main class="page">
    <Card>
      <template #title>
        <div class="header">
          <span>mtlj · stack status</span>
          <Button
            label="Re-run all"
            icon="pi pi-refresh"
            size="small"
            :loading="loading"
            @click="runAll"
          />
        </div>
      </template>
      <template #subtitle>
        Frontend → API at <code>{{ apiBase }}</code> → Postgres / external services
      </template>
      <template #content>
        <Message
          v-if="error"
          severity="error"
          :closable="false"
        >
          {{ error }}
        </Message>

        <DataTable
          :value="rows"
          :loading="loading && rows.length === 0"
          data-key="path"
        >
          <Column
            header="Status"
            style="width: 7rem"
          >
            <template #body="{ data }">
              <Tag
                :value="data.status"
                :severity="severity[data.status as CheckStatus]"
              />
            </template>
          </Column>
          <Column
            field="name"
            header="Check"
          />
          <Column header="Route">
            <template #body="{ data }">
              <code>{{ data.method }} {{ data.path }}</code>
            </template>
          </Column>
          <Column
            header="HTTP"
            style="width: 5rem"
          >
            <template #body="{ data }">
              {{ data.httpStatus ?? '—' }}
            </template>
          </Column>
          <Column
            header="Latency"
            style="width: 6rem"
          >
            <template #body="{ data }">
              {{ data.latencyMs === null ? '—' : `${data.latencyMs} ms` }}
            </template>
          </Column>
          <Column header="Detail">
            <template #body="{ data }">
              <div class="detail">
                {{ data.detail || data.description }}
              </div>
            </template>
          </Column>
          <Column style="width: 3.5rem">
            <template #body="{ data }">
              <Button
                icon="pi pi-play"
                text
                rounded
                size="small"
                :aria-label="`Run ${data.name}`"
                @click="runCheck(data)"
              />
            </template>
          </Column>
        </DataTable>
      </template>
    </Card>
  </main>
</template>

<style scoped>
.page {
  max-width: 72rem;
  margin: 2rem auto;
  padding: 0 1rem;
  font-family: system-ui, sans-serif;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.detail {
  font-size: 0.875rem;
  word-break: break-word;
}
</style>
