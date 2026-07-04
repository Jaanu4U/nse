# ---- Build stage ----
FROM eclipse-temurin:21-jdk-alpine AS builder
WORKDIR /app
COPY gradlew settings.gradle.kts build.gradle.kts ./
COPY gradle/ gradle/
RUN chmod +x gradlew && ./gradlew dependencies --no-daemon --quiet 2>/dev/null || true
COPY src/ src/
RUN ./gradlew bootJar --no-daemon -x test

# ---- Runtime stage ----
FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
RUN addgroup -S nse && adduser -S nse -G nse
COPY --from=builder /app/build/libs/*.jar app.jar
USER nse
EXPOSE 8090
ENTRYPOINT ["java", "-jar", "-Xmx2g", "app.jar"]
