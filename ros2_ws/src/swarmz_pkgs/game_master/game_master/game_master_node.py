import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Int32
from swarmz_interfaces.msg import Detections, Detection
from game_master.utils.tools import get_all_namespaces, get_distance, get_robot_position, get_relative_position
from mavros_msgs.srv import CommandLong
import time
import threading
import os
from ament_index_python.packages import get_package_share_directory
from swarmz_interfaces.srv import UpdateHealth

class GameMasterNode(Node):

    def __init__(self):
        super().__init__('game_master_node')
        self.set_parameters([rclpy.parameter.Parameter('use_sim_time', rclpy.Parameter.Type.BOOL, True)])

        # Declare parameters with default values
        self.declare_parameter('drone_detection_range', 10.0)
        self.declare_parameter('ship_detection_range', 20.0)
        self.declare_parameter('drone_communication_range', 15.0)
        self.declare_parameter('ship_communication_range', 30.0)
        self.declare_parameter('drone_health', 100)
        self.declare_parameter('ship_health', 200)
        self.declare_parameter('drone_points', 10)
        self.declare_parameter('ship_points', 50)
        self.declare_parameter('game_duration', 300)  # 5 minutes

        self.drone_detection_range = self.get_parameter('drone_detection_range').get_parameter_value().double_value
        self.ship_detection_range = self.get_parameter('ship_detection_range').get_parameter_value().double_value
        self.drone_communication_range = self.get_parameter('drone_communication_range').get_parameter_value().double_value
        self.ship_communication_range = self.get_parameter('ship_communication_range').get_parameter_value().double_value
        self.drone_health = self.get_parameter('drone_health').get_parameter_value().integer_value
        self.ship_health = self.get_parameter('ship_health').get_parameter_value().integer_value
        self.drone_points = self.get_parameter('drone_points').get_parameter_value().integer_value
        self.ship_points = self.get_parameter('ship_points').get_parameter_value().integer_value
        self.game_duration = self.get_parameter('game_duration').get_parameter_value().integer_value

        # Get list of all namespaces
        self.namespaces = get_all_namespaces(self)

        # Initialize health points for each robot
        self.health_points = {ns: self.drone_health if 'px4_' in ns else self.ship_health for ns in self.namespaces}

        # Initialize team points
        self.team_points = {'team_1': 0, 'team_2': 0}

        # Define teams
        self.team_1 = ['px4_1', 'px4_2', 'px4_3', 'px4_4', 'px4_5', 'flag_ship_1']
        self.team_2 = ['px4_6', 'px4_7', 'px4_8', 'px4_9', 'px4_10', 'flag_ship_2']

        # Publishers for health points, detections, and communications
        self.health_publishers = {ns: self.create_publisher(Int32, f'{ns}/health', 10) for ns in self.namespaces}
        self.detection_publishers = {ns: self.create_publisher(Detections, f'{ns}/detections', 10) for ns in self.namespaces}
        self.communication_publishers = {ns: self.create_publisher(String, f'{ns}/out_going_messages', 10) for ns in self.namespaces}

        # Subscribers for communications
        self.communication_subscribers = {ns: self.create_subscription(String, f'{ns}/incoming_messages', lambda msg, ns=ns: self.communication_callback(msg, ns), 10) for ns in self.namespaces}

        # Timer to periodically publish detections
        self.timer = self.create_timer(1.0, self.timer_callback)

        # Timer to track game duration
        self.start_time = time.time()
        self.game_timer = self.create_timer(1.0, self.game_timer_callback)

        # Create the service to update health
        self.update_health_srv = self.create_service(UpdateHealth, 'update_health', self.update_health_callback)

    def timer_callback(self):
        """
        Periodically publish detections for each robot.
        """
        threads = []
        for ns in self.namespaces:
            thread = threading.Thread(target=self.publish_detections, args=(ns,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

    def publish_detections(self, ns):
        """
        Publish detections for a specific robot.
        :param ns: The namespace of the robot.
        """
        detections_msg = Detections()
        detections_msg.header.stamp = self.get_clock().now().to_msg()
        detections_msg.detections = self.get_detections(ns)
        self.detection_publishers[ns].publish(detections_msg)

    def game_timer_callback(self):
        """
        Track game duration and end the game if the time is up or a flagship is destroyed.
        """
        elapsed_time = time.time() - self.start_time
        if elapsed_time >= self.game_duration:
            self.end_game()
        else:
            for ship in ['flag_ship_1', 'flag_ship_2']:
                if self.health_points[ship] <= 0:
                    self.end_game()

    def end_game(self):
        """
        End the game and announce the winner.
        """
        self.get_logger().info('Game Over')
        result = "Game Over\n"
        if self.team_points['team_1'] > self.team_points['team_2']:
            result += 'Team 1 wins!\n'
        elif self.team_points['team_1'] < self.team_points['team_2']:
            result += 'Team 2 wins!\n'
        else:
            result += 'It\'s a draw!\n'

        result += f"Team 1 points: {self.team_points['team_1']}\n"
        result += "Team 1 alive robots: " + ", ".join([ns for ns in self.team_1 if self.health_points[ns] > 0]) + "\n"
        result += f"Team 2 points: {self.team_points['team_2']}\n"
        result += "Team 2 alive robots: " + ", ".join([ns for ns in self.team_2 if self.health_points[ns] > 0]) + "\n"

        package_share_directory = get_package_share_directory('game_master')
        result_file = os.path.join(package_share_directory, 'resource', 'game_results.txt')
        game_number = 1
        if os.path.exists(result_file):
            with open(result_file, 'r') as file:
                content = file.read()
                game_number = content.count("--- Game") + 1
            with open(result_file, 'a') as file:
                file.write(f"\n--- Game {game_number} ---\n")
                file.write(result)
        else:
            with open(result_file, 'w') as file:
                file.write(f"--- Game {game_number} ---\n")
                file.write(result)

        rclpy.shutdown()

    def is_friend(self, ns1, ns2):
        """
        Determine if two robots are friends.
        :param ns1: Namespace of the first robot.
        :param ns2: Namespace of the second robot.
        :return: True if they are friends, False otherwise.
        """
        return (ns1 in self.team_1 and ns2 in self.team_1) or (ns1 in self.team_2 and ns2 in self.team_2)
    
    def get_detections(self, ns):
        """
        Get detections for the specified robot.
        :param ns: The namespace of the robot.
        :return: List of detections.
        """
        detections = []
        positions = {ns: get_robot_position(self, ns) for ns in self.namespaces}
        transmitter_position = positions[ns]
        detection_range = self.drone_detection_range if 'px4_' in ns else self.ship_detection_range
        
        for robot in self.namespaces:
            if robot == ns:
                continue
            receiver_position = positions[robot]
            distance = get_distance(transmitter_position, receiver_position)
            if distance <= detection_range:
                detection = Detection()
                detection.vehicle_type = Detection.DRONE if 'px4_' in robot else Detection.SHIP
                detection.is_friend = self.is_friend(ns, robot)
                detection.relative_position = get_relative_position(transmitter_position, receiver_position)
                detections.append(detection)
        
        return detections

    def communication_callback(self, msg, sender_ns):
        """
        Handle incoming communication messages.
        :param msg: The communication message.
        :param sender_ns: The namespace of the sender robot.
        """
        # Get the position of the sender robot
        sender_position = get_robot_position(self, sender_ns)
        
        # Determine the communication range based on the type of the sender robot
        communication_range = self.drone_communication_range if 'px4_' in sender_ns else self.ship_communication_range
        
        # Iterate through all namespaces to find potential receivers
        for ns in self.namespaces:
            if ns == sender_ns:
                continue  # Skip the sender itself
            
            # Get the position of the receiver robot
            receiver_position = get_robot_position(self, ns)
            
            # Calculate the distance between the sender and the receiver
            distance = get_distance(sender_position, receiver_position)
            
            # If the receiver is within the communication range, forward the message
            if distance <= communication_range:
                string_msg = String()
                string_msg.data = msg.data
                self.communication_publishers[ns].publish(string_msg)

    def get_health(self, ns):
        """
        Get the health of the specified robot.
        :param ns: The namespace of the robot.
        :return: The health of the robot.
        """
        return self.health_points.get(ns, 0)

    def update_health_callback(self, request, response):
        """
        Handle the update health service request.
        :param request: The service request containing the robot namespace and damage.
        :param response: The service response.
        :return: The updated response.
        """
        ns = request.robot_name
        damage = request.damage
        self.update_health(ns, damage=damage)
        response.success = True
        return response

    def update_health(self, ns, health=None, damage=0):
        """
        Update the health of the specified robot by setting a new health value or reducing it by a given damage amount.
        :param ns: The namespace of the robot.
        :param health: The health value to set (optional).
        :param damage: The amount of damage to apply (optional).
        """
        if damage > 0:
            current_health = self.get_health(ns)
            health = max(0, current_health - damage)
        
        if health is not None:
            self.health_points[ns] = health
            self.get_logger().info(f'{ns} health is now {health}')
            health_msg = Int32()
            health_msg.data = health
            self.health_publishers[ns].publish(health_msg)
            if health == 0:
                self.update_team_points(ns)
                if 'px4_' in ns:
                    self.get_logger().info(f'{ns} is destroyed, awarding {self.drone_points} points')
                else:
                    self.get_logger().info(f'{ns} is destroyed, awarding {self.ship_points} points')
                if 'px4_' in ns:
                    self.kill_drone(ns)

    def update_team_points(self, ns):
        """
        Update the team points based on the destruction of a robot.
        :param ns: The namespace of the destroyed robot.
        """
        if ns in self.team_1:
            self.team_points['team_2'] += self.drone_points if 'px4_' in ns else self.ship_points
        elif ns in self.team_2:
            self.team_points['team_1'] += self.drone_points if 'px4_' in ns else self.ship_points

    def kill_drone(self, ns):
        """
        Send a PX4 command to kill or shut down the drone.
        :param ns: The namespace of the drone.
        """
        # Send a PX4 command to kill or shut down the drone
        self.get_logger().info(f'Sending kill command to {ns}')
        client = self.create_client(CommandLong, f'{ns}/mavros/cmd/command')
        while not client.wait_for_service(timeout_sec=1.0):
            self.get_logger().warn(f'Waiting for {ns}/mavros/cmd/command service...')
        request = CommandLong.Request()
        request.command = 400  # MAV_CMD_COMPONENT_ARM_DISARM
        request.param1 = 0  # Disarm
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self, future)
        if future.result() is not None:
            self.get_logger().info(f'{ns} disarmed successfully')
        else:
            self.get_logger().error(f'Failed to disarm {ns}')

def main(args=None):
    rclpy.init(args=args)
    game_master_node = GameMasterNode()
    try:
        rclpy.spin(game_master_node)
    except KeyboardInterrupt:
        pass
    finally:
        game_master_node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
