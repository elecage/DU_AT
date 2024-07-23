const int trigPin = 11;  // Trigger 핀1
const int echoPin = 10;  // Echo 핀1
const int trigPin2 = 3;  // Trigger 핀2
const int echoPin2 = 2;  // Echo 핀2
const int LED1 = 5;
const int LED2 = 6;
const int LED3 = 7;
const int buzzer = 4;

unsigned long previousMillis = 0;
unsigned long buzzerInterval = 0;

// 거리 값을 평균화하기 위한 변수
const int numReadings = 5;
long readings1[numReadings];
long readings2[numReadings];
int readIndex = 0;
long total1 = 0;
long total2 = 0;
long average1 = 0;
long average2 = 0;

void setup() {
  Serial.begin(9600);  // 시리얼 통신 시작
  pinMode(trigPin, OUTPUT);  // Trigger1 핀을 출력으로 설정
  pinMode(echoPin, INPUT);   // Echo1 핀을 입력으로 설정
  pinMode(trigPin2, OUTPUT);  // Trigger2 핀을 출력으로 설정
  pinMode(echoPin2, INPUT);   // Echo2 핀을 입력으로 설정
  pinMode(LED1, OUTPUT);
  pinMode(LED2, OUTPUT);
  pinMode(LED3, OUTPUT);
  pinMode(buzzer, OUTPUT);

  // 초기화
  for (int i = 0; i < numReadings; i++) {
    readings1[i] = 0;
    readings2[i] = 0;
  }
}

void loop() {
  long duration1, distance1;
  long duration2, distance2;
  long distance;

  // 1번 센서 초음파 신호 보내기 (JSN_B02는 20us)
  digitalWrite(trigPin, LOW);  
  delayMicroseconds(2); 
  digitalWrite(trigPin, HIGH); 
  delayMicroseconds(20); 
  digitalWrite(trigPin, LOW); 
  
  // 1번 초음파 반사 시간 측정
  duration1 = pulseIn(echoPin, HIGH);

  // 2번 센서 초음파 신호 보내기
  digitalWrite(trigPin2, LOW);  
  delayMicroseconds(2); 
  digitalWrite(trigPin2, HIGH); 
  delayMicroseconds(20); 
  digitalWrite(trigPin2, LOW); 
  
  // 2번 초음파 반사 시간 측정
  duration2 = pulseIn(echoPin2, HIGH);

  // 거리 계산 (cm 단위)
  distance1 = duration1 * 0.034 / 2;
  distance2 = duration2 * 0.034 / 2;

  // 이전 읽기를 빼고 새로운 읽기를 추가
  total1 = total1 - readings1[readIndex];
  total2 = total2 - readings2[readIndex];
  readings1[readIndex] = distance1;
  readings2[readIndex] = distance2;
  total1 = total1 + readings1[readIndex];
  total2 = total2 + readings2[readIndex];

  // 인덱스를 증가시키고, 인덱스가 배열의 끝에 도달하면 다시 처음으로
  readIndex = readIndex + 1;
  if (readIndex >= numReadings) {
    readIndex = 0;
  }

  // 평균 계산
  average1 = total1 / numReadings;
  average2 = total2 / numReadings;

  // 두 센서 간 거리 값 중 가장 작은 값을 기준으로 장애물 위협을 판단
  distance = min(average1, average2);

  // 거리 값이 400cm 이상 2cm 이하일 때는 감지하지 않음
  if (distance >= 400 || distance <= 2) {
    Serial.println("1000");
  } else {
    // 센서의 최소 탐지 거리 값이 20cm이기 때문에 25cm 이상일 때 알고리즘이 동작
    if (distance > 25) {
      unsigned long currentMillis = millis();
      // 탐지 거리가 150cm 이상일 때는 buzzer off & LED off
      if (distance > 140) {
        digitalWrite(LED1, LOW);
        digitalWrite(LED2, LOW);
        digitalWrite(LED3, LOW);
        noTone(buzzer);
        buzzerInterval = 0;
      }
      // 탐지 거리가 110cm 이상일 때는 buzzer on & LED 1개 on
      else if (distance > 110) {
        digitalWrite(LED1, LOW);
        digitalWrite(LED2, LOW);
        digitalWrite(LED3, LOW);
        if (currentMillis - previousMillis >= buzzerInterval) {
          previousMillis = currentMillis;
          tone(buzzer, 900, 50);
          buzzerInterval = 400;  // 400ms 간격으로 소리 발생
        }
      }
      // 탐지 거리가 80cm 이상일 때는 buzzer on & LED 1개 on
      else if (distance > 80) {
        digitalWrite(LED1, HIGH);
        digitalWrite(LED2, LOW);
        digitalWrite(LED3, LOW);
        if (currentMillis - previousMillis >= buzzerInterval) {
          previousMillis = currentMillis;
          tone(buzzer, 900, 50);
          buzzerInterval = 300;  // 300ms 간격으로 소리 발생
        }
      }
      // 탐지 거리가 50cm 이상일 때는 buzzer on & LED 2개 on
      else if (distance > 50) {
        digitalWrite(LED1, HIGH);
        digitalWrite(LED2, HIGH);
        digitalWrite(LED3, LOW);
        if (currentMillis - previousMillis >= buzzerInterval) {
          previousMillis = currentMillis;
          tone(buzzer, 900, 50);
          buzzerInterval = 100;  // 100ms 간격으로 소리 발생
        }
      }
      // 탐지 거리가 50cm 이하일 때는 buzzer on & LED 3개 on
      else {
        digitalWrite(LED1, HIGH);
        digitalWrite(LED2, HIGH);
        digitalWrite(LED3, HIGH);
        if (currentMillis - previousMillis >= buzzerInterval) {
          previousMillis = currentMillis;
          tone(buzzer, 900, 1000);
          buzzerInterval = 10;  // 10ms 간격으로 소리 발생
        }
      }
      Serial.println(distance);
    } else {
      Serial.println("0");
    }
  }

  delay(80);
}