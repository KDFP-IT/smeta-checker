<script setup lang="ts">
const backend = useBackend(),
  route = useRoute(),
  password = ref(''),
  showPassword = ref(false),
  loginName = ref(''),
  mode = ref<'local' | 'ldap' | null>(null),
  error = ref(''),
  busy = ref(false),
  checking = ref(true)
const corporate = computed(() => mode.value === 'ldap')
const back = computed(() => safeInternalRedirect(route.query.back))
async function login() {
  if (busy.value || !mode.value) return
  if (!loginName.value.trim() || !password.value) return
  busy.value = true
  error.value = ''
  try {
    await backend.connect(password.value, loginName.value.trim())
    await navigateTo(back.value, { replace: true })
  } catch (e) {
    error.value = readableError(e)
  } finally {
    busy.value = false
    password.value = ''
  }
}
onMounted(async () => {
  try {
    const gate = await backend.gate()
    mode.value = gate.mode
  } catch (e) {
    error.value = readableError(e)
  } finally {
    checking.value = false
  }
})
</script>

<template>
  <div class="login-page">
    <main id="main-content" class="login-shell" tabindex="-1">
      <div class="login-center">
        <UCard class="login-card">
          <h1 class="login-heading">Вход в систему</h1>
          <p v-if="checking" class="empty" role="status">Проверяем доступ…</p>
          <template v-else>
            <form v-if="mode" class="login-form" @submit.prevent="login">
              <UFormField :label="corporate ? 'Корпоративная почта' : 'Логин'" name="login">
                <UInput
                  v-model="loginName"
                  name="username"
                  @update:model-value="error = ''"
                  :aria-label="corporate ? 'Корпоративная почта' : 'Логин'"
                  :placeholder="corporate ? 'Введите корпоративную почту' : 'Введите ваш логин'"
                  :autocomplete="corporate ? 'email' : 'username'"
                  :leading-icon="corporate ? 'i-lucide-mail' : 'i-lucide-user'"
                  autocapitalize="none"
                  :spellcheck="false"
                  :disabled="busy"
                  autofocus
                  :color="error ? 'error' : 'primary'"
                  :class="{ 'login-error-input': error }"
                  class="w-full"
                />
              </UFormField>
              <UFormField label="Пароль" name="password">
                <UInput
                  id="password"
                  v-model="password"
                  :type="showPassword ? 'text' : 'password'"
                  aria-label="Пароль"
                  placeholder="Введите ваш пароль"
                  autocomplete="current-password"
                  leading-icon="i-lucide-lock"
                  :disabled="busy"
                  :color="error ? 'error' : 'primary'"
                  :class="{ 'login-error-input': error }"
                  class="w-full"
                  :ui="{ trailing: 'pe-1' }"
                >
                  <template #trailing>
                    <UButton
                      color="neutral"
                      variant="link"
                      size="sm"
                      :icon="showPassword ? 'i-lucide-eye-off' : 'i-lucide-eye'"
                      :aria-label="showPassword ? 'Скрыть пароль' : 'Показать пароль'"
                      :aria-pressed="showPassword"
                      aria-controls="password"
                      type="button"
                      @click="showPassword = !showPassword"
                    />
                  </template>
                </UInput>
              </UFormField>
              <UButton
                type="submit"
                :loading="busy"
                :disabled="!loginName.trim() || !password || busy"
                block
              >
                Войти
              </UButton>
            </form>
          </template>
        </UCard>
      </div>
    </main>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100dvh;
  background: var(--ui-bg-muted);
}
.login-shell {
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.login-center {
  width: 100%;
  max-width: 420px;
}
.login-card {
  padding: 32px;
}
.login-heading {
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 20px;
  color: var(--ui-text);
  text-align: center;
}
.login-form {
  display: grid;
  gap: 16px;
}
/* Hide the native password reveal button in Edge/IE. */
::-ms-reveal {
  display: none;
}
@media (max-width: 640px) {
  .login-card {
    padding: 24px;
  }
  .login-center {
    max-width: 100%;
  }
}

.login-error-input {
  animation: login-error-shake 360ms ease-in-out;
}
.login-error-input :deep(input) {
  --tw-ring-color: var(--ui-error) !important;
  border-color: var(--ui-error);
}
@keyframes login-error-shake {
  0%,
  100% {
    transform: translateX(0);
  }
  20% {
    transform: translateX(-4px);
  }
  40% {
    transform: translateX(4px);
  }
  60% {
    transform: translateX(-3px);
  }
  80% {
    transform: translateX(3px);
  }
}
</style>
